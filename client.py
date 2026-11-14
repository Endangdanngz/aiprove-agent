"""ArenaClient — klien REST kecil dan tangguh untuk arena AIProve.

Keputusan desain:
- Hanya memakai stdlib Python (urllib) + eth-account untuk tanda tangan.
- Setiap request JSON di-retry sampai 4x pada kegagalan koneksi / HTTP 5xx
  (egress jaringan bisa terputus di tengah jalan; 4xx tidak di-retry karena
  itu jawaban final server).
- Unduhan teks yang lebih besar (skill.md) memakai strategi *range-resume*:
  byte yang sudah diterima disimpan, lalu sisa byte diminta dengan header
  Range sampai panjang total sesuai Content-Length. Ini membuat loop tetap
  bersih walaupun koneksi putus di tengah unduhan.
- Token sesi dan private key TIDAK pernah ditulis ke log.
"""
import http.client
import json
import time
import urllib.error
import urllib.request

from eth_account.messages import encode_defunct

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) aiprove-agent/1.0"


class ArenaError(RuntimeError):
    pass


class ArenaClient:
    def __init__(self, base_url, log=print):
        self.base = base_url.rstrip("/")
        self.log = log
        self.token = None
        self.address = None

    # ------------------------------------------------------------------ HTTP
    def _resume_bytes(self, url, headers, max_rounds=20):
        """Unduh satu body respons GET secara utuh dengan range-resume.

        Koneksi boleh putus di tengah: byte yang sudah diterima disimpan dan
        sisanya diminta dengan header Range. Mengembalikan (status, bytes).
        """
        buf = b""
        total = None
        status = None
        for _ in range(max_rounds):
            h = dict(headers)
            if buf:
                h["Range"] = f"bytes={len(buf)}-"
            req = urllib.request.Request(url, headers=h, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    status = resp.status
                    if resp.status == 200:
                        clen = int(resp.headers.get("Content-Length") or 0)
                        if buf and clen and total and clen != total:
                            buf = b""  # server mengabaikan Range; mulai ulang
                        total = clen or total
                        buf += resp.read()
                    else:  # 206 Partial Content
                        crange = resp.headers.get("Content-Range", "")
                        if "/" in crange:
                            total = int(crange.rsplit("/", 1)[1])
                        buf += resp.read()
            except urllib.error.HTTPError as e:
                raw = e.read()
                if e.code >= 500:
                    time.sleep(1.5)
                    continue
                return e.code, raw
            except http.client.IncompleteRead as e:
                buf += e.partial
            except Exception:
                time.sleep(1.5)  # putus sebelum/di tengah respons; resume lagi
            if total is not None and len(buf) >= total:
                return status or 200, buf
        raise ArenaError(f"unduhan tidak utuh setelah {max_rounds} ronde: {url} ({len(buf)}/{total})")

    def request(self, method, path, body=None, auth=False, retries=4):
        """Request JSON dengan retry. Mengembalikan (status, payload).

        GET dibaca lewat jalur range-resume (respons daftar bisa belasan KB
        dan koneksi tidak selalu stabil); POST memakai retry biasa karena
        responsnya kecil."""
        url = self.base + path
        headers = {"User-Agent": USER_AGENT}
        if auth:
            if not self.token:
                raise ArenaError("belum terautentikasi")
            headers["Authorization"] = "Bearer " + self.token
        if method == "GET" and body is None:
            status, raw = self._resume_bytes(url, headers)
            text = raw.decode(errors="replace")
            try:
                return status, json.loads(text)
            except json.JSONDecodeError:
                return status, {"raw": text}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        last_exc = None
        for attempt in range(retries):
            try:
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read().decode()
                    try:
                        return resp.status, json.loads(raw)
                    except json.JSONDecodeError:
                        return resp.status, {"raw": raw}
            except urllib.error.HTTPError as e:
                raw = e.read().decode(errors="replace")
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {"raw": raw}
                if e.code >= 500 and attempt < retries - 1:
                    time.sleep(2 ** attempt + 1)
                    continue
                return e.code, payload
            except Exception as e:  # koneksi putus, timeout, dll -> retry
                last_exc = e
                if attempt < retries - 1:
                    time.sleep(2 ** attempt + 1)
                    continue
        raise ArenaError(f"request gagal setelah {retries} percobaan: {method} {path}: {last_exc}")

    def fetch_text(self, path, max_rounds=16):
        """Unduh teks dengan range-resume sampai lengkap sesuai Content-Length."""
        url = self.base + path
        buf = b""
        total = None
        for _ in range(max_rounds):
            headers = {"User-Agent": USER_AGENT}
            if buf:
                headers["Range"] = f"bytes={len(buf)}-"
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    if resp.status == 200:
                        total = int(resp.headers.get("Content-Length") or 0) or total
                        if buf and int(resp.headers.get("Content-Length") or 0) != (total or 0):
                            buf = b""  # server mengabaikan Range; mulai ulang dari respons ini
                        buf += resp.read()
                    else:  # 206 Partial Content
                        crange = resp.headers.get("Content-Range", "")
                        if "/" in crange:
                            total = int(crange.rsplit("/", 1)[1])
                        buf += resp.read()
            except http.client.IncompleteRead as e:
                buf += e.partial
            except Exception:
                time.sleep(1.5)  # koneksi putus; byte yang ada dipertahankan, lanjut resume
            if total is not None and len(buf) >= total:
                return buf.decode()
        raise ArenaError(f"gagal mengunduh {path} secara utuh ({len(buf)}/{total} byte)")

    # ------------------------------------------------------------------ Auth
    def authenticate(self, account):
        """Handshake keypair: challenge -> EIP-191 sign -> verify -> sesi."""
        self.address = account.address
        status, ch = self.request("GET", f"/api/auth/challenge?address={self.address}")
        if status != 200 or not ch.get("challenge"):
            raise ArenaError(f"challenge gagal: HTTP {status}")
        self.log(f"      challenge: HTTP {status}")
        signature = account.sign_message(encode_defunct(text=ch["challenge"])).signature.hex()
        if not signature.startswith("0x"):
            signature = "0x" + signature
        status, res = self.request(
            "POST", "/api/auth/verify", {"address": self.address, "signature": signature}
        )
        if status != 200 or not res.get("token"):
            raise ArenaError(f"verify gagal: HTTP {status}")
        self.token = res["token"]
        self.log(f"      verify: HTTP {status} (token sesi diterima, tidak dicetak)")
        return res

    # ---------------------------------------------------------------- Arena
    def list_open_bounties(self):
        status, data = self.request("GET", "/api/bounties?status=open")
        if status != 200:
            raise ArenaError(f"list bounties gagal: HTTP {status}")
        return data.get("bounties", [])

    def enter(self, slug, handle):
        return self.request("POST", f"/api/bounties/{slug}/enter", {"handle": handle}, auth=True)

    def submit_repo(self, slug, repo_url, commit, transcript, handle):
        body = {"repoUrl": repo_url, "commit": commit, "transcript": transcript, "handle": handle}
        return self.request("POST", f"/api/bounties/{slug}/submit", body, auth=True)

    def submit_bughunt(self, slug, bugs, transcript, handle):
        """Kirim laporan bug. Bentuk payload dicoba dari yang paling lengkap;
        HTTP 400 berarti bentuk ditolak dan TIDAK ada submission yang dibuat,
        jadi aman mencoba bentuk berikutnya."""
        attempts = [
            {"bugs": bugs, "handle": handle, "transcript": transcript},
            {"bugs": bugs, "handle": handle},
            {"bugs": bugs},
            {"reports": bugs, "handle": handle, "transcript": transcript},
            bugs,  # terakhir: body berupa JSON list mentah
        ]
        for i, body in enumerate(attempts, 1):
            status, res = self.request("POST", f"/api/bounties/{slug}/submit", body, auth=True)
            self.log(f"      submit {slug} percobaan-{i}: HTTP {status}")
            if status != 400:
                return status, res
        return status, res

    def me(self):
        return self.request("GET", "/api/me", auth=True)
