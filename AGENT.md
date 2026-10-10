# AGENT.md — 圖片 → Blender 場景：agent 執行手冊

你是執行這條管線的 agent。照本文件由上而下做，每一步都有明確的輸入、輸出與通過條件。
「看圖」的步驟由你親自用 Read 工具讀圖判斷，不要跳過，也不要用檔案大小或程式輸出代替目視檢查。

## 0. 任務定義

| | |
|---|---|
| **輸入** | 一張場景圖（放在專案根目錄，例如 `test.jpg`） |
| **輸出** | `scene.blend`（貼圖已打包）、`work/renders/view_ref.png`、`view_overview.png`、`compare.png` |
| **完成條件** | 場景內至少 10 種不同物件；每個物件至少生成並審核過 4 張 2D 圖；每個 3D 模型都經過檢視；地面與牆面有材質、地面有合適的散佈物；最終渲染與原圖構圖對得上 |
| **回報** | 結尾交付一份總結：物件清單與做法、哪些不合格及原因、與原圖的差異、耗時 |

路徑約定：`$P` = 專案在 WSL 內的路徑（例如 `/mnt/d/UNIDENTIFIED/3D_test`）；`$PW` = 同一路徑的 Windows 寫法。

## 1. 環境與硬性限制

| 元件 | 位置 | 呼叫方式 |
|---|---|---|
| WSL Ubuntu | 使用者 `xxroyxx`，虛擬磁碟在 D 槽（HDD） | `wsl -e bash -lc '...'` |
| TRELLIS.2 | `~/TRELLIS.2`，conda env `trellis2` | 由 `pipeline/trellis_loop.sh` 呼叫 |
| ComfyUI + FLUX.2 klein | `~/ComfyUI`，conda env `comfyui` | `pipeline/comfy_start.sh` / `comfy_stop.sh` |
| Blender 5.2 | `D:\Blender Foundation\Blender 5.2\blender.exe` | `blender -b -P <script> -- <args>` |

硬性限制，違反會出事：

1. **GPU 只有 12 GB，ComfyUI 與 TRELLIS/RMBG 不能同時佔用。** 生成 2D 圖期間開 ComfyUI；進入去背或 TRELLIS 之前一定先跑 `comfy_stop.sh`。
2. **不要在 `trellis2` 環境安裝或升級任何套件。**
3. **下載或產生大檔前，先看 Windows 磁碟實際剩餘空間**（Git Bash：`df -h /c /d`）。WSL 內的 `df` 顯示的是虛擬上限，不可信。
4. **不要用會比對到自己的 `pkill -f` / `pgrep -f`**（指令字串本身含有樣式時會殺掉自己）。用 `comfy_stop.sh`，或把樣式第一個字母包成 `[x]`。
5. **不要印出 `~/.cache/huggingface/token` 的內容**，也不要列出含有該 token 的程序指令列。
6. 長時間工作（TRELLIS）用背景執行，並監看 `work/trellis.log` 的 `[done]` / `[FAIL]` 行。
7. TRELLIS 執行期間只能跑 `--preview` 的 Blender 與 `06_inspect_models.py`（Workbench 引擎，幾乎不佔 VRAM），不要跑完整渲染。

時間參考（RTX 3060）：ComfyUI 冷啟動後第一張圖 2–5 分鐘，之後 9B 約 30 秒/張、4B 約 15 秒/張；TRELLIS 載入約 5 分鐘，每個物件 2–16 分鐘。

## 2. 流程總覽

```
步驟 1  看圖 → 寫 scene.json（物件、bbox、深度、初始 prompt）
步驟 2  01_make_refs.py → 每個物件的參考裁切圖 ref.png
步驟 3  逐物件迭代生成 2D 圖：生成 → 看圖 → 決定是否改 prompt → 再生成（至少 4 張）
步驟 4  寫 work/selection.json → 03_select_matte.py matte → 檢查去背
步驟 5  trellis_loop.sh → 06_inspect_models.py → 逐一檢視 3D 模型，不合格的處理
步驟 6  05_build_scene.py --preview → 調整擺位
步驟 6b 表面材質與散佈物（地板、牆面材質；碎石、雜物）→ 渲染 → 看圖 → 調參數
步驟 6c 完整渲染 → 燈光微調 → 對照原圖
步驟 7  總結回報
```

## 3. 步驟 1：看圖並撰寫 scene.json

1. 用 Read 看原圖，記下圖片尺寸。
2. 先建立只含 `image`、`image_size` 的最小 `scene.json`，然後產生座標格線圖：

   ```bash
   wsl -e bash -lc 'source ~/miniconda3/etc/profile.d/conda.sh; conda activate trellis2; python $P/pipeline/00_grid.py $P'
   # 需要放大特定區域時： python $P/pipeline/00_grid.py $P 330,270,510,369 0,150,300,369
   ```

   讀 `work/debug/scene_grid.png` 和 `zoom_*.png`，格線數字是**原圖像素座標**。
3. 列出物件並填入 `scene.json`。

**選物件的規則**

- 至少 10 種不同物件，主角之外也要有道具與細節（燈具、招牌、箱子、支架等）。
- 重複出現的同款物件只列一次，其餘用 `instances`。
- 大面積結構（地板、牆、舞台、窗戶、前景遮擋）不走 img-to-3D，寫進 `environment` 與 `props`，由 Blender 程式生成。
- 建築本身需要精確重建（樑、柱、階梯、路緣等不只是平面）時，設 `"environment": {"type": "script", "script": "scene_env.py"}`，
  在專案根目錄寫一個該場景專用的 Python 檔：定義 `build(ctx)`，可選 `lights(ctx)`、`support_height(xy)`（回傳該點的地面高度，
  用於高台與花台）、`scatter_allowed(xy)`。`ctx` 提供 `material`、`surface`（讀 `environment.surfaces.<名稱>`）、相機模型等輔助函式。
  先用 `--preview` 渲染並與原圖疊圖比對輪廓，再調整尺寸。
- 帶文字的平面物件（招牌、標示牌）設 `"mode": "image_slab"`：TRELLIS 會把文字重建成亂碼，這類物件改用正面 2D 圖做成立體浮雕板。

**scene.json 結構**（完整範例見專案現有的 `scene.json`）

```jsonc
{
  "image": "test.jpg",
  "image_size": [656, 369],
  "description": "一句話描述場景、光線、視角",
  "camera": {"hfov_deg": 50, "height_m": 1.4},
  "generation": {
    "model": "9b",              // 9b 品質較好（非商用授權）；4b 較快且可商用
    "candidates": 4,            // 每個物件最少張數
    "megapixels": 1.0,
    "suffix": "……白底、平光、無陰影等共用要求，會自動接在每個 prompt 後面"
  },
  "objects": [
    {
      "id": "huggy",                       // 小寫英文加底線，也是資料夾名
      "bbox": [333, 96, 439, 364],         // 物件在原圖的緊貼外框 [x1,y1,x2,y2]
      "depth_m": 12.0,                     // 沿相機前方的距離估計（公尺）
      "prompt": "Extract the ... from this image. ...",   // 初始 prompt，英文
      "trellis": {"faces": 300000, "texture": 2048}
    }
  ],
  "environment": { "anchor": "huggy", "stage": {...}, "hall": {...}, "balcony_ring": {...},
                   "surfaces": {...} },   // 表面材質，見步驟 6b
  "scatter": [ ... ],                     // 地面散佈物，見步驟 6b
  "props": [ {"id": "wall_red", "bbox": [...], "depth_m": 18.0, "thick_m": 0.3, "color": [r,g,b]} ],
  "lights": { "key_spot": {...}, "fog_density": 0.009 }
}
```

物件的可選欄位：

| 欄位 | 用途 |
|---|---|
| `ref_bbox` | 參考裁切範圍（預設是 bbox 外擴 25%）；物件很小或需要更多上下文時指定 |
| `grounded` | `true` 表示底部貼地（或貼舞台面），忽略 bbox 算出的高度 |
| `support` | `true` 表示在物件下方自動加一個深色底座到地面 |
| `yaw_deg` / `roll_deg` | 場景中的水平旋轉 / 傾斜 |
| `fit` | `"inside"` 表示整個模型縮到 bbox 內；物件姿勢與原圖不同時用 |
| `instances` | 同款物件的其他位置：`[{"bbox": [...], "depth_m": ..., "yaw_deg": ...}]` |
| `seed_base` | 種子起點（預設 1000），想整組重來時換一個 |
| `mode: "image_slab"` | 文字招牌；搭配 `slab_image`（預設用 `"rgba.png"`）、`slab_thickness`、`slab_relief` |
| `trellis.pipeline_type` | 預設 `"1024_cascade"`；**實心方塊類（箱子、積木、櫃子）必須設 `"512"`**，否則 VRAM 不足會 CUDA 錯誤 |
| `trellis.seed` | 3D 重做時換種子 |
| `pos_m` | 已知真實位置時直接指定底部中心的世界座標 `[x, y]`（貼地）或 `[x, y, z]`，取代由 bbox 與深度反推的位置；`instances` 也可用 |
| `width_m` / `height_m` | 已知真實尺寸時直接指定，取代由 bbox 推算的縮放；`instances` 也可用 |
| `pitch_deg` | 繞水平軸旋轉；`image_slab` 設 `-90` 可平放在地上（報紙、海報） |
| `emission` / `emission_threshold` | 讓物件以自身貼圖顏色發光（招牌、螢幕）；`emission_threshold` > 0 時只有貼圖亮部發光 |

相機的 `camera.pitch_deg` 可直接指定俯仰角（例如由地平線或消失點算出），省略時由主角腳底位置反推。

深度估計原則：先定主角的距離，其他物件依前後遮擋關係相對給值；同一平面上的東西給相同深度。寬度（公尺）≈ bbox 像素寬 ÷ 703 × 深度（hfov 50°、圖寬 656 時），用它檢查尺寸是否合理。

**輸出**：`scene.json`。**通過條件**：JSON 可解析；物件 ≥ 10；每個 bbox 都對照格線圖確認過。

## 4. 步驟 2：產生參考裁切圖

```bash
wsl -e bash -lc 'source ~/miniconda3/etc/profile.d/conda.sh; conda activate trellis2; python $P/pipeline/01_make_refs.py $P'
```

**輸出**：`work/objects/<id>/ref.png`（已放大、提亮）。逐一 Read 確認目標物件確實在畫面內；不在就修 `bbox` / `ref_bbox` 後對該物件重跑（指令後面加物件 id）。

## 5. 步驟 3：迭代生成 2D 圖（核心步驟）

啟動 ComfyUI（一次即可，整個步驟 3 期間保持開啟）：

```bash
wsl -e bash -lc 'bash $P/pipeline/comfy_start.sh'      # 印出 COMFY_READY 才算成功
```

對**每個物件**執行下面的迴圈，一次只生成一張：

```
n = 0
loop:
  生成一張   →  02_generate.py --next <id> [--prompt "..."] [--review-prev "..."]
  Read 這張 cand_n.png，同時對照 ref.png
  依「審核清單」寫下評語
  若有缺點   →  針對缺點改寫 prompt，下一輪帶 --prompt 與 --review-prev
  若已夠好   →  不改 prompt，下一輪只帶 --review-prev（換種子多拿一張備選）
  結束條件   →  已有 ≥ 4 張，且其中至少 1 張通過審核清單
  上限       →  8 張仍沒有合格的，選最接近的一張並在總結中註明
```

指令：

```bash
# 第 1 張：用 scene.json 的初始 prompt
wsl -e bash -lc 'python3 $P/pipeline/02_generate.py $P --next crate_g'

# 之後每一張：先寫對上一張的評語；要改 prompt 才加 --prompt
wsl -e bash -lc 'python3 $P/pipeline/02_generate.py $P --next crate_g \
  --review-prev "cube ok but letter G only on one face, slightly cropped at bottom" \
  --prompt "Extract the pale cube ... a large dark grey capital letter G on every visible face. The whole cube fully inside the frame with empty margin."'
```

- `--prompt` 省略時沿用上一張的 prompt（只換種子）。
- 每次呼叫都會把 prompt、種子、耗時寫進 `work/objects/<id>/prompts.json`；`--review-prev` 的評語記在上一筆，形成完整的修改理由紀錄。
- `generation.suffix` 會自動接在 prompt 後面，不要重複寫白底、平光這類共用要求。

**審核清單**（每張都要逐項看）

| 項目 | 不合格的樣子 |
|---|---|
| 單一物件 | 畫面裡多了別的東西、殘留原場景的一部分（例如玻璃上映出別的物件） |
| 完整 | 被畫面邊緣裁掉、被遮擋的部分沒補上 |
| 像原物件 | 顏色、形狀、姿勢、數量（例如觸角根數、手指）與 ref 明顯不符 |
| 背景乾淨 | 不是純白、有地面、有明顯投影 |
| 光線 | 帶有原場景的色光或霧氣 |
| 適合 3D | 純正面看不出厚度（招牌除外）、極端透視、細長部件糊在一起 |
| 文字（若有） | 拼錯、缺字、變形 |

**改寫 prompt 的原則**

- 一次只針對看到的缺點改，不要整段重寫；保留已經有效的描述。
- 缺什麼就明講：「both rods fully inside the frame」「exactly two antennae」「nothing in front of the legs」。
- 模型沒照做時換說法或加強調，不要只是重複同一句。
- 立體物件要求 three-quarter view；`image_slab` 的招牌要求 straight-on front view。
- 被遮擋的物件要寫明遮擋物並要求移除：「Remove the monster's legs」。
- 已經合格就不要再改 prompt；之後的張數只是換種子取得備選。

**輸出**：每個物件 `cand_0.png … cand_k.png`（k ≥ 3）與 `prompts.json`。全部物件完成後：

```bash
wsl -e bash -lc 'bash $P/pipeline/comfy_stop.sh'       # 印出 COMFY_STOPPED 才能進下一步
```

## 6. 步驟 4：選圖與去背

產生對照表並逐一 Read：

```bash
wsl -e bash -lc 'source ~/miniconda3/etc/profile.d/conda.sh; conda activate trellis2; python $P/pipeline/03_select_matte.py $P sheet'
# → work/objects/<id>/sheet.png（左起：ref、cand_0、cand_1、…）
```

寫 `work/selection.json`，每個物件一筆：

```json
{
  "huggy": {"pick": 2, "why": "姿勢與 ref 相符，四肢分明；cand 3 舉起的手變形"},
  "sign":  {"pick": 1, "why": "正面、字體清晰"}
}
```

去背並檢查：

```bash
wsl -e bash -lc 'source ~/miniconda3/etc/profile.d/conda.sh; conda activate trellis2; python $P/pipeline/03_select_matte.py $P matte'
# → rgba.png（透明背景）、review.png（左：原圖，右：洋紅底去背結果）
```

Read 每個 `review.png`。**通過條件**：物件完整、沒有殘留陰影或白邊大塊、細長部件（桿、觸角）沒被吃掉。不合格就改選別張，對該物件重跑 `matte <id>`。

## 7. 步驟 5：生成與檢視 3D 模型

```bash
# 背景執行；失敗會自動重啟並跳過已完成的物件
wsl -e bash -lc 'bash $P/pipeline/trellis_loop.sh $P 8 > $P/work/trellis.log 2>&1'
```

監看 `work/trellis.log`：`[done] <id> in Ns`、`[FAIL] <id>`、`[all done]`、`[gave up after 8 attempts]`。

完成後產生檢視圖並逐一 Read：

```bash
"D:/Blender Foundation/Blender 5.2/blender.exe" -b -P pipeline/06_inspect_models.py -- "$PW"
# → work/inspect/<id>.png（左：正面，右：背面斜角）
```

| 看到的問題 | 處理方式 |
|---|---|
| 同一物件反覆 `[FAIL]`（CUDA error、device not ready） | 在 `trellis` 加 `"pipeline_type": "512"`，重跑 loop |
| 文字或字母變成亂碼 | 改成 `"mode": "image_slab"`；需要一張正面 2D 圖，沒有就回步驟 3 用正面視角重新生成 |
| 形狀明顯錯誤、破面、多出雜物 | 刪掉該物件的 `model.glb`，在 `trellis` 設不同的 `"seed"` 重跑；仍不行就回步驟 3 換一張 2D 圖 |
| 貼圖略糊但形狀正確，且是遠處小道具 | 可接受，在總結中註明 |

重做單一物件：刪除 `work/objects/<id>/model.glb` 後重跑 `trellis_loop.sh`。每個物件最多重做 2 次，之後就接受並註明。

**輸出**：每個非 `image_slab` 物件的 `work/objects/<id>/model.glb`，以及 `work/inspect/<id>.png`。

## 8. 步驟 6：組裝 Blender 場景

先用預覽模式檢查擺位（快、不佔 VRAM、不存檔）：

```bash
"D:/Blender Foundation/Blender 5.2/blender.exe" -b -P pipeline/05_build_scene.py -- "$PW" --preview
# → work/renders/preview_ref.png、preview_overview.png
```

Read `preview_ref.png` 並與原圖比對，逐項檢查後修改 `scene.json` 再重跑：

| 問題 | 調整 |
|---|---|
| 物件左右或上下位置偏移 | 修 `bbox` |
| 太大或太小 | 修 `bbox` 或 `depth_m`；姿勢與原圖不同的物件加 `"fit": "inside"` |
| 浮在空中 | `grounded: true`，或 `support: true` 加底座 |
| 前後遮擋關係錯 | 調 `depth_m` |
| 面向不對 | `yaw_deg`；斜角生成的模型用 `model_yaw_deg` 先轉正 |
| 背景空洞 | 在 `props` 加色塊（牆面、看板、前景遮擋） |

### 步驟 6b：表面材質與散佈物

擺位確認後，替程式生成的表面加材質，並在地面撒上小雜物。兩者都寫在 `scene.json`，由 `05_build_scene.py` 讀取；不需要另外跑腳本。這一步的效果只有完整渲染看得到（`--preview` 不顯示材質）。

**1. 看原圖判斷需要什麼。** 地面是什麼（磁磚、水泥、木地板、泥土、碎石地）？牆面呢？地上有沒有散落物（碎石、落葉、紙張、小玩具）？原圖看不清楚時，依場景類型給合理的預設，並在總結中註明是推測。

**2. 設定表面材質**，寫在 `environment.surfaces`。可設定的表面：`floor`、`stage`、`hall_wall`、`balcony`。

```jsonc
"surfaces": {
  "floor":     {"preset": "tiles", "color": [0.10, 0.11, 0.15], "color2": [0.05, 0.055, 0.08], "scale_m": 0.8},
  "hall_wall": {"preset": "painted", "color": [0.08, 0.09, 0.14], "scale_m": 3.0},
  "balcony":   {"polyhaven": "concrete_floor_02", "scale_m": 2.0, "tint": [0.5, 0.55, 0.7]}
}
```

| 來源 | 寫法 | 何時用 |
|---|---|---|
| 程序化預設（優先） | `"preset"`: `plain`、`concrete`、`tiles`、`painted`、`metal`、`gravel`、`wood` | 不需下載、風格容易統一；先試這個 |
| Poly Haven 貼圖（CC0） | `"polyhaven": "<asset id>"` | 預設做不出來的質感（磨損地板、磚牆、岩石） |

共用欄位：`color`（基色）、`scale_m`（**真實尺寸，公尺**：磁磚邊長、木板寬、碎石粒徑，或貼圖重複一次的寬度）、`roughness`、`tint`（只對 Poly Haven，用來把色調拉向原圖）。

使用 Poly Haven 時：

```bash
python pipeline/polyhaven.py search concrete floor      # 找 asset id
python pipeline/polyhaven.py size concrete_floor_02 1k  # 先看檔案大小，不下載
python pipeline/polyhaven.py get "$PW" concrete_floor_02 1k   # 下載到 work/textures/<id>/
```

下載前先告訴使用者要下載什麼、來源、大小。預設用 1k（每組約 1–5 MB）；2k 以上只在特寫需要時才用。只用 Poly Haven（CC0）；其他來源授權不一，不要自行加入。

**3. 設定散佈物**，寫在最上層的 `scatter`：

```jsonc
"scatter": [
  {"id": "rubble", "kind": "rocks", "count": 60, "size_m": [0.03, 0.1],
   "radius_m": 8.5, "inner_radius_m": 3.3, "colors": [[0.2, 0.2, 0.23]], "seed": 7}
]
```

| 欄位 | 說明 |
|---|---|
| `kind` | `rocks`（不規則石塊）、`blocks`（倒角小方塊）、`papers`（平貼地面的紙張） |
| `count` | 數量；全場景合計建議不超過 500 |
| `size_m` | 尺寸範圍 `[最小, 最大]`，公尺 |
| `radius_m` / `inner_radius_m` | 以主角為圓心的散佈環帶；用 `inner_radius_m` 把舞台或主角腳邊留空 |
| `colors` | 顏色清單，省略時用該種類的預設色 |
| `margin_m` | 與既有物件保持的距離（預設 0.15） |
| `seed` | 換一個值就換一種分布 |

散佈物是共用網格的實例，並會自動避開所有已放好的模型與道具，所以一定要在擺位確定之後才調這一步。

**4. 渲染、看圖、調整。** 跑完整渲染（指令見下方步驟 6c），Read `view_ref.png` 和 `view_overview.png`，對照原圖檢查：

| 看到的問題 | 調整 |
|---|---|
| 紋理顆粒大得像巨石，或小到變成雜訊 | 改 `scale_m`（它是公尺，對照場景裡物件的真實大小） |
| 看得出貼圖重複 | 加大 `scale_m`，或改用程序化預設 |
| 色調和原圖不合 | 改 `color`；Poly Haven 材質改 `tint` |
| 地面太亮或反光太強 | 調暗 `color`、提高 `roughness` |
| 雜物太搶眼、太整齊或太稀疏 | 改 `count`、`size_m`、`colors`、`seed` |
| 雜物出現在不該有的地方 | 調 `radius_m` / `inner_radius_m` |
| 原圖沒有的東西 | 拿掉；不要為了豐富而加上會改變場景氣氛的元素 |

最多調 3 輪。**通過條件**：表面看得出材質且尺度合理、沒有明顯重複；散佈物沒有穿過任何物件；整體色調沒有偏離原圖。

### 步驟 6c：完整渲染與燈光

確定 ComfyUI 與 TRELLIS 都已停止，跑完整渲染：

```bash
"D:/Blender Foundation/Blender 5.2/blender.exe" -b -P pipeline/05_build_scene.py -- "$PW"
# → scene.blend、work/renders/view_ref.png、view_overview.png
```

Read 兩張渲染圖。光線太亮、太暗或霧太濃時調 `lights`（`key_spot.energy_w`、`fog_density`），最多調 3 輪。
最後產生原圖與渲染的左右對照圖：

```bash
wsl -e bash -lc 'source ~/miniconda3/etc/profile.d/conda.sh; conda activate trellis2; python $P/pipeline/07_compare.py $P'
# → work/renders/compare.png
```

**通過條件**：主要物件的位置、大小、遮擋關係與原圖一致；沒有物件懸空或穿插；文字可讀。

## 9. 步驟 7：總結回報

交付時說明：

- 物件表：每個物件的做法（TRELLIS / 512 / image_slab / 程式生成）、生成了幾張 2D 圖、prompt 改了幾次、最終品質。
- 表面材質與散佈物：各表面用了哪個預設或哪個 Poly Haven 素材（附 asset id）、撒了什麼、哪些是原圖看不清而推測的。
- 不合格或妥協的項目與原因。
- 與原圖的已知差異（深度是估計值、被遮擋處是模型補的）。
- 各階段耗時（可從 `work/generation_log.jsonl` 和 `work/trellis.log` 取得）。
- 確認 ComfyUI 與 TRELLIS 都已停止。

只回報實際看過、驗證過的結果；沒檢查的項目要明說沒檢查。

## 10. 檔案對照

```
scene.json                      場景定義（你撰寫與修改）
pipeline/00_grid.py             座標格線圖
pipeline/01_make_refs.py        參考裁切圖
pipeline/02_generate.py         FLUX.2 生成（--next 迭代模式 / 批次模式）
pipeline/comfy_edit.py          ComfyUI API 客戶端（被 02 呼叫）
pipeline/comfy_start.sh|stop.sh 啟動 / 停止 ComfyUI
pipeline/03_select_matte.py     對照表（sheet）與去背（matte）
pipeline/04_trellis_batch.py    TRELLIS.2 批次（被 loop 呼叫）
pipeline/trellis_loop.sh        TRELLIS 自動重啟迴圈
pipeline/05_build_scene.py      組裝 Blender 場景（--preview 為預覽）
pipeline/scene_surfaces.py      表面材質預設與散佈物（被 05 匯入）
scene_env.py                    場景專用的建築腳本（environment.type 為 "script" 時由 05 載入）
pipeline/polyhaven.py           搜尋 / 下載 Poly Haven CC0 貼圖
pipeline/06_inspect_models.py   3D 模型檢視圖
pipeline/07_compare.py          原圖與渲染的對照圖
work/objects/<id>/              ref.png、cand_*.png、prompts.json、sheet.png、rgba.png、review.png、model.glb
work/selection.json             選圖結果與理由（你撰寫）
work/textures/<asset id>/       下載的 Poly Haven 貼圖
work/inspect/, work/renders/    檢視圖與渲染圖
```
