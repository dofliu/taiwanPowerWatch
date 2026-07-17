# 待辦 · TODO

## 已知限制（非程式問題，暫無法單純改程式碼解決）

- [ ] 備轉容量率即時來源（`sys_dem_sup.csv`）在 GitHub Actions 雲端 CI 的 IP 上會被台電 WAF
      擋掉（403），瀏覽器 header 也繞不過去，因此目前 `docs/grid_status.json` 幾乎都是退到
      每日備援（資料集 19995），觀察落後可達 1～2 個月。前端／App 通知已誠實標示
      `granularity:"daily"` 與「非即時，截至 YYYY-MM-DD」，不會冒充即時值。
      要真正解決需要換一個不會被 WAF 擋的執行環境（例如自架 runner），不是單純改程式碼能解決；
      與風電風情（windfarmTaiwan）共用同一段邏輯與同一限制。

## 待評估／待使用者決定方向（不要自作主張動工）

- [ ] 若上述 WAF 問題有解，備轉容量率的長期趨勢／每日摘要可比照發電趨勢圖一併呈現
- [ ] 是否需要上架 Google Play（需開發者帳號＋正式簽章，目前僅 CI 建置 debug APK）
