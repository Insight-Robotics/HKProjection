# HKProjection — WGS84 ↔ 香港1980方格網座標（三維轉換）

[English](README.md) · **繁體中文**

在 WGS84／ITRF96 大地地理座標與香港1980方格網座標之間進行轉換，並且包含高程。
專案提供桌面圖形介面、CSV 與 shapefile 批次轉換工具，以及可供程式呼叫的 Python
函式庫。

本專案完整實作地政總署測繪處公佈的整條轉換鏈，而非只做平面投影，並補上一般通用
函式庫欠缺的一環：**香港高程基準面（HKPD）**高程轉換。

| | |
|---|---|
| 平面精度 | 與 `pyproj` EPSG:2326 相比，74 個控制點上最大差異 **1.4 厘米** |
| HKPD 高程精度 | 留一交叉驗證 **0.023 米 rms** |
| 適用範圍 | 北緯 22.199 – 22.550，東經 113.853 – 114.372 |
| 相依套件 | 核心函式庫無需任何套件；圖形介面需 tkinter，shapefile 需 `pyshp` |

---

## 為何不直接用 pyproj？

`pyproj` 的平面轉換沒有問題 — `EPSG:4326 → EPSG:2326` 是正確的，本專案與它的
結果相差約一厘米。它做不到的是**高程**。

香港1980方格網是二維投影系統。任何高程經過 `pyproj` 流程後仍然是**橢球高**，
因為香港高程基準面並沒有公開的 EPSG 大地水準面格網檔。橢球高與香港每一張測量
圖則上的水平高程相差 2 至 4 米，而且差值在全港各處並不相同。

本專案以官方公佈的控制點資料填補這個空隙：

```
h_HKPD = H_WGS84 − N_sep(φ, λ)
```

---

## 快速開始

需要 Python 3.7 或以上版本（開發及測試環境為 3.9）。

```bash
git clone https://github.com/Insight-Robotics/HKProjection.git
cd HKProjection
python hkgui.py
```

核心函式庫與圖形介面只需 Python 標準函式庫。以下為選用套件，各自只對應一項
功能：

```bash
pip install pyshp        # 圖形介面與批次工具的 shapefile 支援
pip install pyproj       # 只用於 validate.py 的獨立交叉驗證
pip install pdfplumber   # 只用於從 PDF 重新產生控制點 CSV
```

隨時可以驗證安裝是否正常：

```bash
python hk1980.py selftest     # 官方文件所載的參考例子
python validate.py            # 完整驗證流程
```

---

## 圖形介面

```bash
python hkgui.py
```

以 tkinter 建構，共四個分頁：

- **Single point（單點轉換）** — 兩個方向皆可，輸入座標即得結果。經緯度接受十進
  制度數或度分秒，常見寫法大致都能辨識：`22.3193`、`22 19 09.5 N`、
  `22°19'09.5"N`、`N22 19 09.5`、`22d19m09.5s`、`114 10 42.80W`。結果會列出
  方格網座標、兩種高程、高程分離值，以及中間的 HK80 數值。
- **Batch file（批次檔案）** — 轉換整個 CSV 或 shapefile，詳見下文。
- **Height (HKPD)（高程轉換）** — 在指定位置換算橢球高 ↔ HKPD 高程，位置可用
  WGS84 經緯度或 HK1980 方格網 N／E 提供。
- **About（關於）** — 轉換鏈、精度數據與注意事項。

每個分頁都有 *Restrict to Height Model coverage*（限制於高程模型覆蓋範圍）
的勾選項。除非確實需要外推到香港以外，否則請保持勾選。

### 批次轉換

選定輸入檔案與轉換方向，其餘設定會自動判斷，也可以手動覆寫。

- **CSV** — 座標與高程欄位會依欄名自動偵測（`lat`／`latitude`／`y`、
  `northing`、`h`／`height`／`z`／`elevation` 等），也可從下拉選單重新指定。
  原有欄位會保留，計算結果另外附加。轉換失敗的資料列仍然會寫出，失敗原因記錄
  在 `conv_status` 欄，不會靜靜消失。
- **Shapefile** — 座標取自幾何圖形。點、線、面圖層都支援：所有節點都會轉換，
  並保留 part 結構。點圖層會把計算結果附加為屬性欄位。高程來源依次為：含 Z 值
  圖層的 Z 座標、使用者指定的屬性欄位，或 0。輸出時會一併產生 `.prj` 檔。

輸出格式跟隨輸入格式，輸出路徑亦會自動建議。轉換在背景執行緒進行並顯示進度列，
因此處理大型檔案時視窗不會卡住。

同一個引擎可供程式呼叫，本身亦附有命令列介面：

```bash
python hkbatch.py in.csv out.csv --to hk1980
python hkbatch.py points.shp out.shp --to wgs84 --height-type hkpd
python hkbatch.py in.csv out.csv --to hk1980 --lat-col Y --lon-col X --height-col ELEV
python hkbatch.py points.shp out.shp --to hk1980 --ignore-z
```

---

## 函式庫用法

```python
from hk1980 import wgs84_to_hk1980, hk1980_to_wgs84, ELLIPSOIDAL, HKPD

r = wgs84_to_hk1980(22.3193, 114.1694, 25.0)   # 緯度、經度、橢球高
r.N                   # 820032.934   香港1980方格網 N（米）
r.E                   # 835497.922   香港1980方格網 E（米）
r.h_hkpd              #     27.738   香港高程基準面以上高程（米）
r.ellipsoidal_height  #     25.000   WGS84 橢球面以上高程（米）
r.separation          #     -2.738   所採用的高程分離值 N_sep（米）
r.hk80_lat, r.hk80_lon, r.hk80_height   # 中間的 HK80 基準數值

b = hk1980_to_wgs84(820032.934, 835497.922, 27.738)   # N、E、HKPD 高程
b.lat, b.lon, b.height                                # 轉回 WGS84
```

兩個函式都接受 `height_type` 參數（`ELLIPSOIDAL` 或 `HKPD`），用以說明**輸入**
高程屬於哪一種基準；而兩者都必定同時回傳兩種高程 — 因此位置與高程基準可以在
同一次呼叫中一併轉換。預設值為：轉往方格網時用 `ELLIPSOIDAL`，轉回時用 `HKPD`，
分別對應各自最常見的情況。

```python
wgs84_to_hk1980(22.3193, 114.1694, 27.738, height_type=HKPD)
hk1980_to_wgs84(820032.9, 835497.9, 25.0, height_type=ELLIPSOIDAL)
```

若只需轉換高程而不涉及位置轉換：

```python
from hk1980 import separation, ellipsoidal_to_hkpd, hkpd_to_ellipsoidal

separation(22.3193, 114.1694)                  # -2.7379 米
ellipsoidal_to_hkpd(22.3193, 114.1694, 25.0)   # 27.738 米
hkpd_to_ellipsoidal(22.3193, 114.1694, 27.738) # 25.000 米
```

位置若超出高程模型覆蓋範圍會拋出 `ValueError`；如需強制外推，可傳入
`strict=False`。

較底層的元件同樣公開：`geodetic_to_cartesian`、`cartesian_to_geodetic`、
`HK80_TO_ITRF96`、`geographic_to_grid`、`grid_to_geographic`、
`meridian_distance`、`footpoint_latitude`，以及參數組 `HK1980_GRID`、
`UTM_WGS84_49Q/50Q`、`UTM_HK80_49Q/50Q`。

命令列：

```bash
python hk1980.py fwd 22.3193 114.1694 25.0    # WGS84 -> 香港1980方格網 + HKPD
python hk1980.py inv 820032.9 835497.9 27.7   # 香港1980方格網 + HKPD -> WGS84
python hk1980.py selftest
```

---

## 演算法

`Reference/SchematicDiagram.pdf` 所繪的轉換鏈：

```
WGS84 / ITRF96 大地地理座標   φ, λ, H            [WGS84 橢球體]
        │
        │  ①  大地座標 → 空間直角座標
        │      X = (ν + H) cos φ cos λ
        │      Y = (ν + H) cos φ sin λ
        │      Z = ((1 − e²) ν + H) sin φ
        ▼
WGS84 空間直角座標            X, Y, Z
        │
        │  ②  七參數 Helmert 轉換（7P_ITRF96_HK80_V1.0，取其逆向）
        │      ⎡X⎤      ⎡ΔX⎤   ⎡(1+S)   θz    −θy ⎤ ⎡X⎤
        │      ⎢Y⎥    = ⎢ΔY⎥ + ⎢ −θz   (1+S)   θx ⎥ ⎢Y⎥
        │      ⎣Z⎦_HK80 ⎣ΔZ⎦   ⎣  θy    −θx  (1+S)⎦ ⎣Z⎦_WGS84
        ▼
HK80 空間直角座標             X, Y, Z
        │
        │  ③  空間直角座標 → 大地座標（φ 為隱式，需疊代求解）
        │      tan λ = Y / X
        │      tan φ = (Z + e² ν sin φ) / √(X² + Y²)
        │      H     = X sec λ sec φ − ν
        ▼
HK80 大地地理座標             φ, λ, H            [國際 1910 橢球體]
        │
        │  ④  橫麥卡托投影，方程式 1 至 3
        │      N = N₀ + m₀{ (M − M₀) + ν sin φ (Δλ²/2) cos φ }
        │      E = E₀ + m₀{ ν Δλ cos φ + ν cos³φ (ψ − t²) Δλ³/6 }
        ▼
香港1980方格網座標            N, E
```

逆向轉換是同一條鏈往上走：投影部分改用方程式 4 及 5，基準轉換則取 Helmert
矩陣的精確逆矩陣。

### 第三個維度

香港1980方格網本身並不包含高程分量。上述轉換鏈確實帶著一個高程走完全程，但在
HK80 一端得出的 `H` 是**國際 1910 橢球體上的橢球高** — 那是基準轉換的副產品，
並非任何測量作業所採用的高程。它以 `hk80_height` 回報，不應當作高程使用。

實際可用的第三維是**香港高程基準面（HKPD）**高程，取得它需要的是高程分離模型，
而不是基準轉換。`N_sep` 由《香港高程模型控制點（第 1.0 版）》的 74 個控制點內插
而得，資料自來源 PDF 擷取至 `hk_height_model.csv`。每個控制點提供一組 ITRF96
橢球高與實測 HKPD 高程。分離值由西南面（大嶼山）的 **−4.06 米** 變化至東面的
**−2.08 米**。

模型採用 (Δλ, Δφ) 的最小二乘二次趨勢面，再對殘差以最近 10 個控制點作反距離加權
內插。

---

## 驗證

執行 `python validate.py` — 全部檢查通過。

**官方參考例子**（《說明》第 C10 頁），香港1980方格網：

| | 計算值 | 官方值 | 差異 |
|---|---|---|---|
| φ,λ → N | 832699.106 | 832699 | +0.11 米 |
| φ,λ → E | 836055.198 | 836055 | +0.20 米 |
| N,E → φ | 22°26′06.7565″ | 22°26′06.76″ | −0.004″ |
| N,E → λ | 114°10′20.4531″ | 114°10′20.45″ | +0.003″ |

**衍生常數**在列印精度內與第 C10 頁一致：兩個橢球體的 ν、ρ、ψ，兩個 e² 值，以及
由方程式 3 算出的 M₀ = 2 468 395.728 米（差 0.2 毫米）。

**基準平移量**重現第 B6 頁數值（dφ = +5.5″、dλ = −8.8″，各 ±0.1″），計算結果為
+5.511″、−8.831″。

**獨立交叉驗證**：在全部 74 個控制點上與 `pyproj` EPSG:2326 比較 — 那是同一基準
與投影的另一套獨立實作：

```
dN   平均 -0.002 米   rms 0.003 米   最大 0.008 米
dE   平均 +0.004 米   rms 0.005 米   最大 0.014 米
```

**HKPD 高程模型**，在 74 個點上作留一交叉驗證（每點由其餘 73 點推算）：

```
平均 +0.002 米   rms 0.023 米   最大 0.061 米
```

**往返轉換**在全部控制點上，三個分量的閉合差都小於 1 微米。

---

## 精度與注意事項

- **平面方向（WGS84 → 香港1980方格網）** — 限制來自基準轉換本身，而非本程式碼。
  《說明》第 C4 頁就基準轉換整體給出 0.2″／5 米的指標；七參數組在實務上遠優於
  此。上文與 pyproj 之間厘米級的差異，源自官方方程式 1／2 的級數截斷於 Δλ³。

- **HKPD 高程** — 交叉驗證約 2.3 厘米 rms，且僅在控制點覆蓋範圍內有效
  （北緯 22.199–22.550，東經 113.853–114.372）。高程模型為 2006 年的 1.0 版；
  該文件註 6 指出當時橢球高有待全面重測，因此分離值其後或已修訂。

- **逆向轉換** — 依示意圖註 \*6，向上計算時必須提供概略高程，而回傳的橢球高只是
  近似值，其精度取決於所提供的高程與分離模型。

- **七參數的出處** — 示意圖點名了參數文件（《Geodetic Datum Transformation and
  Map Projection Parameter Set for Computation between ITRF96 Geodetic
  Coordinates … and HK1980 Grid Coordinates》），但該 PDF **並未包含在本專案內**。
  `HK80_TO_ITRF96` 所用的是已公佈的 `7P_ITRF96_HK80_V1.0` 參數組；本專案以第 B6
  頁的獨立數值及 EPSG:1825 作驗證，兩者完全吻合。若用於測量作業，請先向地政總署
  文件核對。

- **參數組的方向** — 依示意圖所繪的矩陣形式代入，這組數值是由 **HK80 → WGS84**
  （示意圖註 \*3）。註 \*2 的方向須取逆矩陣求得。這是座標框架（EPSG 9607）旋轉
  約定，其正負號模式與較常見的位置向量約定互為轉置，因此切勿在未翻轉旋轉量正負號
  的情況下，把這些參數交給預期 EPSG 9606 的函式庫。

- **UTM** — 專案為完整起見一併收錄 UTM 參數組，但方程式 1 至 5 屬截斷級數，而
  香港距離 50Q 帶中央子午線 117°E 有 2.8°，該處被略去的 Δλ⁴ 項約值 2.3 米的 N。
  第 C10 頁的 UTM 參考例子本身亦互相矛盾約 2 米 — 正向例子印的是 2 483 566 N，
  而逆向例子對名義上同一點卻代入 2 483 568 N — 因此任何忠實實作官方級數的程式都
  不可能同時符合兩者。以高階橫麥卡托投影核對，本程式算出的 E 準確至 2 厘米，
  反而是印出的 209 194 才是異數。**如需處理 UTM，請改用完整的橫麥卡托實作。**
  香港1980方格網因中央子午線正好穿過本港而不受影響：在比香港更大的範圍內，
  正向再逆向的閉合差最大僅 1.6 毫米，若使用
  `grid_to_geographic(..., exact=True)` 則完全閉合。

---

## 檔案

| 路徑 | |
|---|---|
| `hkgui.py` | 桌面圖形介面 — 由此開始 |
| `hk1980.py` | 轉換函式庫，自足完整，只用標準函式庫 |
| `hkbatch.py` | CSV／shapefile 批次引擎，附命令列介面 |
| `hk_height_model.csv` | 由來源 PDF 擷取的 74 個控制點 |
| `validate.py` | 完整驗證流程 |
| `tools/extract_control_points.py` | 由來源 PDF 重新產生 CSV |
| `Reference/` | 地政總署來源文件 |
| `Reference/notes_extracted.txt` | 《說明》的純文字版本，方便搜尋 |

---

## 資料來源

所有公式、參數及控制點資料均來自香港特別行政區政府地政總署測繪處的刊物，
存放於 `Reference/`：

- 《香港大地測量基準說明》（Explanatory Notes on Geodetic Datums in Hong Kong，
  1995 年，2018 年小修訂）— 方程式 1 至 5、投影參數、參考例子、基準平移量。
- 《Schematic Diagram Showing Transformation of Coordinates between WGS84
  Geographic Coordinates and HK 1980 Grid Coordinates》— 轉換鏈。
- 《Control Points for Height Model of Hong Kong (Version 1.0)》（2006 年）—
  HKPD 高程分離模型所依據的 74 個控制點。

上述文件版權屬香港特別行政區政府所有，在此僅供參考之用。本專案為獨立實作，
並未獲地政總署認可。**在用於測量、法定或攸關安全的工作之前，請先與官方來源
核對。**
