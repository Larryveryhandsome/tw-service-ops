"""Authenticated image-only service evidence. Stored in database backups."""
import base64
import hashlib
import io
import warnings
from PIL import Image
import core

def _normalize(raw):
    """手機原檔（HEIC／HEIF／WEBP、或超過 2000 萬像素）→ 轉成 JPEG 長邊 1600（阿偉手機照片瀏覽器解不開，原檔直接傳上來）。"""
    try:
        import pillow_heif; pillow_heif.register_heif_opener()
    except Exception:
        pass
    from PIL import ImageOps
    Image.MAX_IMAGE_PIXELS = 150_000_000
    with Image.open(io.BytesIO(raw)) as im:
        im.draft('RGB', (1600, 1600))
        im = ImageOps.exif_transpose(im).convert('RGB'); im.thumbnail((1600, 1600))
        out = io.BytesIO(); im.save(out, 'JPEG', quality=82); return out.getvalue()

def thumb(raw, size=360):
    """工單照片縮圖（中控台一排縮圖用，原圖最長 1800px，24 張全載太慢）。失敗就回原圖。"""
    try:
        from PIL import ImageOps
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert('RGB'); im.thumbnail((size, size))
        b = io.BytesIO(); im.save(b, 'JPEG', quality=72); return b.getvalue(), 'image/jpeg'
    except Exception:
        return raw, None

def delete(con, aid, by):
    """管理層刪除照片（重複、拍錯）：標記成 deleted（服務前／完工計數、傳業主、作品集都不再算），原檔留在資料庫，誤刪可以救回。"""
    r = con.execute('SELECT * FROM attachments WHERE id=?', (aid,)).fetchone()
    if not r:
        raise ValueError('找不到這張照片')
    if r['label'] == 'deleted':
        return '這張已經刪除了'
    con.execute("UPDATE attachments SET label='deleted',name=? WHERE id=?", (f"{r['name']}（原本：{r['label']}，{by} 刪除）"[:200], aid))
    core.audit(con, 'photo_delete', 'orders', r['order_id'], before={'attachment_id': aid, 'label': r['label']}, after={'by': by})
    return '已刪除這張照片'

def upload(con,p):
    o=core.get(con,'orders',p['order_id'])
    if o['status']=='cancelled':raise ValueError('取消工單不可上傳回報')
    if p.get('label') not in ('before','after','incident','receipt','signin'):raise ValueError('請選擇照片用途')      # 簽到表以前一直被擋（上線後一張都傳不上去）
    core.date(p['service_date'])
    try:
        raw=base64.b64decode(p['content'],validate=True)
        if not raw or len(raw)>10*1024*1024:raise ValueError()
        try:
            with Image.open(io.BytesIO(raw)) as im0:
                ok0=im0.format in ('PNG','JPEG') and im0.width*im0.height<=20000000 and len(raw)<=5*1024*1024
        except Exception:
            ok0=False
        if not ok0:raw=_normalize(raw)          # 手機原檔 → 轉 JPEG
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as im:
                if im.format not in ('PNG','JPEG') or im.width*im.height>20000000:raise ValueError()
                mime='image/png' if im.format=='PNG' else 'image/jpeg'; im.verify()
    except Exception:raise ValueError('這張照片讀不到（檔案可能壞掉或不是照片），請換一張或用相機重拍') from None
    key=core.uid('photo');name=str(p.get('name','服務照片'))[:150]
    con.execute('INSERT INTO attachments(id,order_id,name,mime,bytes,sha256,label,service_date) VALUES(?,?,?,?,?,?,?,?)',
                (key,o['id'],name,mime,len(raw),hashlib.sha256(raw).hexdigest(),p['label'],p['service_date']))
    con.execute('INSERT INTO attachment_blobs VALUES(?,?)',(key,raw))
    core.audit(con,'upload_photo','orders',o['id'],after={'attachment_id':key,'bytes':len(raw),'label':p['label']})
    return {'id':key,'bytes':len(raw)}
