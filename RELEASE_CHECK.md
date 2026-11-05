# tw-service-ops 公開前檢查（RELEASE_CHECK）

- 最新複查：**2026-11-05**（雲端 Linux 全新環境；前一次 2026-10-27 在 Windows 11）
- 目前可見度：**PUBLIC**（本次沒有改 repo 設定或可見度）
- 測試環境（本次）：Linux；Python 3.10.20、3.11.18、3.12.3、3.13.18；從 git 乾淨 clone、每個版本各開新 venv、照 README `pip install -r requirements.txt`
- 測試環境（前次）：Windows 11；Python 3.10.22、3.12.6、3.14.2
- 來源：原系統白名單複製（只讀，來源與線上示範站都沒改、沒停）
- git 歷史：全新歷史；本次修正走 PR，不 force push、不壓 commit

## 結論先講

**可上線。** 本次在 Linux 抓到並修掉 1 個會讓功能整片壞掉的 bug（簽章網址在 Linux／macOS 全部 500）和 3 個輸入錯誤回 500 的小 bug；測試 31 → 35 項，4 個 Python 版本全過。第 3 道（真實 LINE／金流沒實測）仍是示範版已知限制。

| # | 閘門 | 結果 | 一句話（2026-11-05） |
|---|---|---|---|
| 1 | 需求與設計 | ✅ | 角色權限表、外部服務全關的預設、失效退路寫在 README／SECURITY.md；本次補上「非 Windows 的系統簽章金鑰怎麼存」 |
| 2 | 程式本身 | ✅ | 35 項測試全過（3.10／3.11／3.12／3.13）；pyflakes 0 個未定義名稱；`node --check` 全過；pip-audit 0 漏洞 |
| 3 | 串接 | ⚠️ | 示範版封鎖所有對外連線；LINE、LINE Pay、Google、Anthropic 沒有用真實帳號實測（雲端環境也碰不到） |
| 4 | 完成使用者任務 | ✅ | 預約 → 試算 → 送出 → 管理端看得到報價 → 客人看得到報價單；**本次修好**：業主改時間連結在 Linux／macOS 打得開 |
| 5 | 安全／效能／環境 | ✅ | 越權、CSRF、Host／Origin 照舊全擋；全部 55 個 GET、148 個 POST 路由用 4 種身分打一輪：修正前 24 個 500，修正後 0 個 |
| 6 | 資料 | ✅ | 備份 → 完整性檢查 → 拿備份檔實際起 server，資料都在（`test_backup_then_restore`） |
| 7 | 交付版本 | ✅ | 乾淨 clone＋新 venv 照 README 可裝可測可跑；seed → 啟動 → 12 個主要網址 200 → `--stop` 停掉 |
| 8 | 出事能恢復 | ✅ | 啟動時自動備份到 `data/backups/`；重跑 seed 舊庫改名保留；`--stop` 可停 |

## 2026-11-05 複查（Linux 全新環境）

### 本次修的 bug
| # | 問題 | 重現（修正前） | 修正 | 證據 |
|---|---|---|---|---|
| 1 | **Linux／macOS 上所有簽章網址 500**：照片網址、業主改時間、作品集、付款、退訂連結共用的簽章金鑰第一次產生時要存資料庫，`integrations.protect` 沒有 DPAPI 就丟 RuntimeError | `GET /api/public/chg?o=<單號>&s=<正確簽章>` → 500；`/pay/police`、`/pay/shop` 不帶參數也 500 | `put_secret(..., generated=True)`：只有系統自己產生的隨機簽章金鑰（`photo_url_key`、`calendar_feed_token`）在非 Windows 存明碼（`plain:` 前綴）；第三方金鑰在非 Windows 照樣拒存，並改丟 ValueError（畫面看得到「請改用環境變數」的說明，不再是 500） | `test_signed_link_works_without_windows`（正確簽章 200、錯誤簽章 403）、`test_only_generated_keys_stored_without_dpapi` |
| 2 | `/api/public/onboard` 沒帶 `kind` → `kind[0]` IndexError 500 | `GET /api/public/onboard` → 500 | 沒帶 kind 回 403「連結已過期」 | `test_bad_public_requests_are_not_500` |
| 3 | 應徵照片上傳不是圖片 → PIL 例外 500 | `POST /api/public/apply/photo {"content":"<非圖片>"}` → 500 | `recruit.shrink` 讀不出來回 400「這張照片讀不到…」 | 同上 |
| 4 | 還沒人上傳過夥伴檔案時 `/api/worker-file` → `no such table` 500 | 管理員 `GET /api/worker-file?id=x` → 500 | `file_get` 先 `ensure` 建表，找不到回 403 | `test_worker_file_missing_is_not_500` |

**用修正前的程式跑新測試**：4 項全部 FAIL；修正後 35 項全過。

### 指令與結果
| 項目 | 指令 | 結果 |
|---|---|---|
| 全部測試（修正前） | `python -m unittest discover -s tests -v` | 31 項全過（3.10.20／3.11.18／3.12.3／3.13.18）——但現有測試沒有涵蓋簽章網址，所以 bug 1 沒被抓到 |
| 全部測試（修正後） | 同上，乾淨 clone＋新 venv | **35 項全過**（3.10.20／3.11.18／3.12.3／3.13.18） |
| 路由掃描 | 自寫爬蟲：admin／staff／worker／未登入各打 55 個 GET、148 個 POST（空 body），另打 `/api/save/*` 與各種壞 token 網址 | 修正前 25 個 500 → 修正後 0 個 |
| 靜態檢查 | `pyflakes app tests` | 0 個未定義名稱；其餘為沒用到的 import／變數等無害提示 |
| 前端語法 | `node --check app/web/*.js` | 全過 |
| 3.10 語法 | 用 3.10 `ast.parse` 全部 `app/*.py` | 全過 |
| 相依套件漏洞 | `pip-audit -r requirements.txt`、`-r requirements-optional.txt` | No known vulnerabilities found |
| 照 README 啟動 | `seed_demo.py` → `demo_server.py`（`DEMO_PORT`）→ `--stop` | 12 個主要網址 200；停止後連不到 |

### 去識別化（本次）
| 項目 | 結果 |
|---|---|
| 本次 diff 新增的電話、email、網址、IP | 0 筆 |
| 全 repo 電話 | 只有虛構 `0900-000-xxx`、欄位提示 `0912-345-678`、刻意保留的客製化廣告電話 |
| 全 repo email、私鑰、API 金鑰、LINE userId | 0 筆 |
| IP | 只有 `127.0.0.1`、`1.1.1.1`（測試對外封鎖）、`203.0.113.9`（RFC 5737 文件用位址） |
| 殘留的夥伴暱稱 | 程式註解 5 處、畫面範例 1 處（派工流程說明）→ 改成「指定夥伴」等中性說明 |
| 客戶代號 | 註解裡的 `客戶01`～`客戶03` 是前次已代號化的結果，保留 |

## 證據（2026-10-27，Windows）

### 第 2 道 程式本身
| 項目 | 指令 | 結果 |
|---|---|---|
| 全部測試 | `python -m unittest discover -s tests -v` | 31 項全過（Python 3.10.22、3.12.6、3.14.2，皆在乾淨 clone＋新 venv） |
| CI | `.github/workflows/test.yml` | push／PR 自動跑 Python 3.10、3.12 |
| 語法相容 | 用 Python 3.10 parse 全部 `app/*.py` | 原本 `seo.py` 有 3.12 才支援的 f-string 寫法 → 已改，3.10 全過 |
| 前端語法 | `node --check app/web/*.js` | 全過 |
| 靜態檢查 | `pyflakes app tests` | 0 個未定義名稱；剩沒用到的 import 等無害提示；修掉 `seo.py` 重複的字典鍵 |
| 相依套件漏洞 | `pip-audit -r requirements.txt`、`-r requirements-optional.txt` | No known vulnerabilities found |

### 第 4、5 道 實測（真的啟動 server、用 HTTP 打）
| 需求 | 風險 | 測試案例 | 預期 | 結果 |
|---|---|---|---|---|
| 主要頁面可用 | 壞頁 | `/`、`/intro`、`/book`、`/join`、`/mall`、`/privacy`、`/home`、`/store`、`/hi`、`/health`、CSS／JS | 200 | ✅ |
| 免登入 `/enter` | 開放轉址 | `as`＝四種角色、未知角色；`go`＝`quotes`、`../evil`、外部網址、`<script>`、超長、中文；worker 帶 go | 只接受 20 字內英文字母，其餘回 `/`；worker 不吃 go | ✅ |
| `/__demo_stop` 只接受本機 | 遠端關站 | 帶 `X-Forwarded-For`／`Cf-Connecting-Ip`／`Forwarded`、外部 Host、`/__demo_stopx` | 404、server 照常；本機直連 200 並真的結束 | ✅（原本是 `startswith`，`/__demo_stopx` 也會關站，且經 Tunnel 時 client 是 127.0.0.1 → 已修） |
| 對外連線封鎖 | 示範版誤連外部服務 | DNS 查 api.line.me、連 1.1.1.1、urlopen LINE API、連本機其他埠 | 全部被擋，訊息為「示範版已關閉對外連線」 | ✅ |
| `/book` 預約成立報價 | 主流程 | 選項目×2 → 試算 → 送出 → submit | 金額＝單價×數量；stage＝review；管理員報價清單有這筆；客人 `/q/<token>` 200 | ✅ |
| 必填與同意 | 輸入處理、個資 | 沒勾同意、沒有 Origin、電話 2 碼且無姓名 | 4xx | ✅ |
| 未登入 | 未授權 | `/api/state`、`/api/quotes`、`/api/billing`、`/api/users` | 401 | ✅ |
| 服務人員看不到別人 | 越權讀取 | worker 讀 `/api/state`、自己的入口 | 只有自己；其他 5 位姓名與電話都不出現；結算單只有自己的 | ✅ |
| 服務人員不能用管理 API | 越權 | `/api/users`、`/api/billing`、`/api/ledger`、`/api/settlements`、`/api/quotes`、`/api/customers/unbound`、`/api/payhist` | 403 | ✅ |
| 客服不能看帳 | 越權 | staff 讀 `/api/billing`、`/api/ledger`、`/api/payhist` | 403 | ✅ |
| CSRF | 跨站寫入 | 管理員 POST 不帶 `X-CSRF-Token` | 403 | ✅ |
| 服務人員入口 token | 猜網址 | `/api/public/portal/<亂打>` | 4xx | ✅ |
| **重新應徵不能冒用身分（本次修的漏洞）** | 冒用 | 陌生 LINE 帳號＋既有服務人員的電話、`reapply_mode=screen`、對方曾被婉拒 | 綁定核准前：不建審資料卡、照片不掛到對方名下、LINE 不綁；改送「是不是本人」確認；管理層按「是本人」後才審資料、掛照片 | ✅ `test_reapply_with_known_phone_cannot_impersonate`；**用修正前的程式跑同一情境會重現漏洞**（screening=1、照片掛到對方名下），修正後擋下 |
| **客服看不到、改不了服務人員銀行帳戶（本次修的漏洞，HIGH）** | 金流個資外洩／竄改 | staff 讀 `/api/state`；staff 對 `/api/save/workers` 送 bank_name／bank_account／bank_holder／payout_cycle＋備註 | 每位人員這 4 欄都是 null、整包回應找不到帳號；送出後備註有改、銀行 4 欄一個都沒變；管理員照常看得到 | ✅ `test_staff_cannot_see_or_change_worker_bank`；**用修正前的程式跑會失敗**（staff 看到「示範銀行」） |
| 金鑰 | 外洩 | 讀金鑰的順序；掃 repo | 先讀 `OPS_SECRET_<KEY>`，預設空值；repo 內沒有私鑰／API 金鑰／LINE userId | ✅ |

漏洞說明二：`app/server.py` 的 `state_for` 對沒有 money 權限的角色只清掉 `rate`，`/api/save` 的移除清單也沒有銀行欄位。現在用常數 `WORKER_BANK`（bank_name、bank_account、bank_holder、payout_cycle）兩邊一起處理：讀取時設成 null、寫入時移除。服務人員在自己的手機入口仍看得到、改得到**自己的**銀行資料。

漏洞說明一：`app/worker_portal.py` 原本在電話對到既有服務人員時，就直接 `interviews.screen(con, old['id'], …)` 並把 `worker_files` 掛到 `old['id']`。現在只有送出的 LINE 帳號**已經綁定**這位服務人員（`line_users.worker_id == old['id']`）才直接做；否則先存在 settings `rescreen_wait:<帳號>:<LINE>`，等 `line_bot.decide_bind` 核准綁定時由 `worker_portal.rescreen_after_bind` 補做。

### 第 6 道 資料
| 項目 | 做法 | 結果 |
|---|---|---|
| 備份 | 管理員 `POST /api/backup`（要 CSRF；worker 403） | 產生 `data/backups/operations-*.db` ✅ |
| 備份完整 | `PRAGMA integrity_check`＋6 張主要表筆數對照 | ok、筆數一致 ✅ |
| **實際還原** | 把備份檔當資料庫另起一個 server，查剛預約的報價 | 找得到 ✅（`test_backup_then_restore`） |
| 重建示範資料 | 同一資料夾跑兩次 seed | 舊庫改名 `_舊示範資料_*.db` 保留，不刪 ✅ |
| 示範資料虛構 | 全部客戶、人員電話 | 都是 `0900-000-xxx` ✅ |

備份位置改成 `OPS_DATA/backups/`（原本是資料夾的上一層，測試用暫存資料夾時會寫到系統 Temp）。

### 第 7 道 交付版本
| 項目 | 結果 |
|---|---|
| 乾淨 clone 到短路徑、新 venv、`pip install -r requirements.txt`、跑測試 | ✅ 3.10／3.12／3.14 全過 |
| 乾淨 clone 抓到的問題 | ① 測試掃描誤掃 `.venv` → 改成只掃 repo 內容；② `requirements.txt` 有中文註解，繁中 Windows 的舊版 pip 讀不了（cp950）→ 改成純英文；③ 3.10 語法不相容 → 已修 |
| 照 README 啟動示範 | `python app/seed_demo.py`＋`python app/demo_server.py`（另用 `DEMO_PORT` 換埠）主要頁面 200，測完用 `--stop` 停掉 |
| git 歷史 | 全新歷史、沒有舊系統的 commit；作者為 noreply 身分；目前 2 個 commit（初始＋銀行欄位資安修正） |

### 第 8 道 出事能恢復
- 錯誤：伺服器錯誤寫進 `data/logs/`（pythonw 背景執行時）與「功能回報」；前端 JS 錯誤每 IP 每小時最多 10 筆收進功能回報。
- 回復：啟動與手動都會備份；資料庫遷移在 `core.initialize()`：加欄位，或把資料原樣搬到新表再換掉，不刪資料（但升級後無法自動降版，降版請用備份）。
- 停止：`python app/demo_server.py --stop`。

## 去識別化
| 項目 | 結果 |
|---|---|
| 白名單複製 | 只放 `app/*.py`、`app/web/`、`schema*.sql`、`demo_intro.html`；沒有 `data/`、`backups/`、`*.db`、`*.bk*`、`__pycache__`、logs、`online.flag` |
| 關鍵字 grep：品牌、負責人、內部網域、固定 IP、私鑰、真實 LINE userId、清潔類字樣、內部客戶名 | 0 筆 |
| 內部營運對話註解（「10/14 店長：…」、回報編號 R10xxxx） | 約 400 處改寫成中性技術說明；寫死真實客戶 id、真實客戶名的一次性程式分支已移除 |
| 內部識別 | 品牌代碼改中性（`homeline`／`bizline`）；環境變數改 `OPS_*`；本機模型路徑、真實 LINE 加好友短網址、伺服器 IP 移除 |
| `deep_audit.py` 剩下的命中（全部判讀） | 英文字串片段誤判；行政區名誤判；手機號碼＝虛構 `0900-000-xxx` 與欄位提示 `0912-345-678`；NOTICE、README、畫面底部的客製化廣告（刻意保留） |

## ⚠️ 已知問題（寫在 README）
- 真實 LINE、LINE Pay、Google、Anthropic、Web Push 沒有用真實帳號實測。
- 在畫面上存金鑰需要 Windows（DPAPI）；其他系統用環境變數。
- 報價單 PDF 下載與官網伺服器端預先渲染需要的工具程式不在公開版。
- 正式模式沒有附建立第一個管理員的指令。
- 角色代碼 `cleaner` 等英文識別字沿用原程式。

## 發布決定
- [x] 推到 GitHub
- [x] 改成 public（本次複查時已是 public）
- [x] 2026-11-05 Linux 全新環境複查（本 PR）
- [ ] 店長驗收（第 3 道已知限制是否接受）
