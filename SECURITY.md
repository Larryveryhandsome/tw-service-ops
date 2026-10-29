# 資安政策

## 回報漏洞

發現資安問題請**不要**開公開 issue。請到 GitHub 本 repo 的 **Security › Report a vulnerability** 私下回報，
或加 LINE：林其衞 說明（請註明「tw-service-ops 資安」）。原則上 3 個工作天內回覆。

## 設計上的安全措施

- 伺服器只綁 `127.0.0.1`；對外要經過 HTTPS 反向代理，並設定 `OPS_PUBLIC_HOSTS` 白名單（Host 標頭不符一律拒絕）。
- 所有寫入 API 要 Cookie 登入＋`X-CSRF-Token`＋同源 `Origin`；公開表單要同源 `Origin` 與個資同意。
- 角色權限：服務人員只拿得到自己的資料；客服看不到帳務；越權 API 回 403。
- 重新應徵時，電話對到既有服務人員**不會**直接沿用身分，要管理層確認綁定後才審資料、掛照片。
- 金鑰只從環境變數讀（或 Windows DPAPI 加密存資料庫），repo 不附任何金鑰。
- 示範版（`demo_server.py`）封鎖所有對外連線；`/enter` 免登入與 `/__demo_stop` 只存在示範版，**不要把示範版開到網路上當正式系統**。
