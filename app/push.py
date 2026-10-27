# -*- coding: utf-8 -*-
"""（沿用合作品牌 CRM 已驗證版本）手機推播（Web Push）：VAPID 金鑰、RFC 8291 aes128gcm 加密、背景送出佇列。
只用已安裝的 cryptography，不另外裝套件。iPhone 要先「加入主畫面」從主畫面打開才收得到；Android Chrome 直接可以。"""
import base64, json, os, queue, ssl, struct, threading, time, urllib.request, urllib.error
from urllib.parse import urlsplit
from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

SUB = "https://ops.example.com"   # VAPID 聯絡資訊（網站網址）

# Windows 憑證庫缺 Apple 推播伺服器的中繼憑證（iPhone 收不到就是這個），再加上 certifi 的憑證清單
TLS = ssl.create_default_context()
try:
    import certifi
    TLS.load_verify_locations(certifi.where())
except Exception:
    pass


def b64u(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
def ub64u(s): return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _hmac(key, data):
    h = hmac.HMAC(key, hashes.SHA256()); h.update(data); return h.finalize()


def _pub_bytes(pub):
    return pub.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def encrypt(payload, ua_public_b64, auth_b64, as_private=None, salt=None):
    """RFC 8291：回傳要 POST 的 body（header＋密文）。as_private、salt 只在測試時指定"""
    ua_pub_raw, auth = ub64u(ua_public_b64), ub64u(auth_b64)
    ua_pub = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub_raw)
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    as_pub_raw = _pub_bytes(as_private.public_key())
    salt = salt or os.urandom(16)
    shared = as_private.exchange(ec.ECDH(), ua_pub)
    prk_key = _hmac(auth, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_pub_raw + as_pub_raw + b"\x01")[:32]
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    ct = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack(">I", 4096) + bytes([len(as_pub_raw)]) + as_pub_raw + ct


class Vapid:
    def __init__(self, path):
        if os.path.exists(path):
            self.key = serialization.load_pem_private_key(open(path, "rb").read(), None)
        else:
            self.key = ec.generate_private_key(ec.SECP256R1())
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.public = b64u(_pub_bytes(self.key.public_key()))

    def header(self, endpoint):
        u = urlsplit(endpoint)
        seg = lambda o: b64u(json.dumps(o, separators=(",", ":")).encode())
        signing = seg({"typ": "JWT", "alg": "ES256"}) + "." + seg({"aud": "%s://%s" % (u.scheme, u.netloc), "exp": int(time.time()) + 12 * 3600, "sub": SUB})
        r, s = decode_dss_signature(self.key.sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
        return "vapid t=%s.%s, k=%s" % (signing, b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big")), self.public)


def send_one(vapid, sub, data, ttl=86400):
    """回傳 (HTTP 狀態碼, 錯誤說明)；404/410 代表這支手機的訂閱已失效；0 是連不上"""
    body = encrypt(json.dumps(data, ensure_ascii=False).encode(), sub["p256dh"], sub["auth"])
    req = urllib.request.Request(sub["endpoint"], data=body, method="POST", headers={
        "TTL": str(ttl), "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream",
        "Urgency": "normal", "Authorization": vapid.header(sub["endpoint"])})
    try:
        with urllib.request.urlopen(req, timeout=15, context=TLS) as r: return r.status, ""
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"")[:200].decode("utf-8", "replace")
    except Exception as e:
        return 0, str(e)[:200]


class Sender:
    """背景佇列：notify() 立刻回來，真正送出在另一條執行緒，送失敗不影響網站"""
    def __init__(self, vapid, db_factory):
        self.vapid, self.db, self.q = vapid, db_factory, queue.Queue()
        threading.Thread(target=self._loop, daemon=True).start()

    def push(self, user_ids, data):
        self.q.put((list(user_ids), data))

    def _loop(self):
        while True:
            uids, data = self.q.get()
            try:
                c = self.db()
                subs = c.execute("SELECT * FROM push_subs WHERE user_id IN (%s)" % ",".join("?" * len(uids)), uids).fetchall() if uids else []
                for s in subs:
                    code, why = send_one(self.vapid, s, data)
                    for wait in (3, 10):          # 偶爾查不到網址（getaddrinfo failed）→ 等一下再送，不要整則漏掉
                        if code != 0: break
                        time.sleep(wait); code, why = send_one(self.vapid, s, data)
                    if code in (404, 410):
                        c.execute("DELETE FROM push_subs WHERE id=?", (s["id"],))
                    elif 200 <= code < 300:
                        c.execute("UPDATE push_subs SET last_ok=datetime('now','localtime'), fails=0, last_err=NULL WHERE id=?", (s["id"],))
                    else:
                        c.execute("UPDATE push_subs SET fails=fails+1, last_err=? WHERE id=?", ("%s %s" % (code, why), s["id"]))
                c.commit(); c.close()
            except Exception:
                pass
