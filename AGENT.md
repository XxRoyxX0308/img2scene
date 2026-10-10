# AGENT.md — 圖片 → Blender 場景：agent 執行手冊

你是執行這條管線的 agent。照本文件由上而下做，每一步都有明確的輸入、輸出與通過條件。
「看圖」的步驟由你親自用 Read 工具讀圖判斷，不要跳過，也不要用檔案大小或程式輸出代替目視檢查。

## 0. 任務定義

| | |
|---|---|
| **輸入** | 一張場景圖（放在專案根目錄，例如 `test.jpg`） |
| **輸出** | `scene.blend`（貼圖已打包）、`work/renders/view_ref.png`、`view_overview.png`、`compare.png` |
| **完成條件** | 場景內至少 10 種不同物件；每個物件至少生成並審核過 4 張 2D 圖；每個 3D 模型都經過檢視；建築依原圖重建（不是幾個平面）；地面與牆面有材質、地面有合適的散佈物；最終渲染與原圖構圖對得上 |
| **回報** | 結尾交付一份總結：物件清單與做法、哪些不合格及原因、與原圖的差異、耗時 |

路徑約定：`$P` = 專案在 WSL 內的路徑（例如 `/mnt/d/UNIDENTIFIED/3D_test`）；`$PW` = 同一路徑的 Windows 寫法。

## 1. 環境與硬性限制

| 元件 | 位置 | 呼叫方式 |
|---|---|---|
| WSL Ubuntu | 使用者 `xxroyxx`，虛擬磁碟在 D 槽（HDD） | `wsl -e bash -lc '...'` |
| TRELLIS.2 | `~/TRELLIS.2`，conda env `trellis2` | 由 `pipeline/trellis_loop.sh` 呼叫 |
| ComfyUI + FLUX.2 klein | `~/ComfyUI`，conda env `comfyui` | `pipeline/comfy_start.sh` / `comfy_stop.sh` |
| Blender 5.2 | 使用者開著的 Blender，已啟用 MCP for Blender 外掛 | Blender MCP 工具：`get_addon_status`、`get_scene_info`、`execute_blender_code`、`look` |

硬性限制，違反會出事：

1. **GPU 只有 12 GB，ComfyUI 與 TRELLIS/RMBG 不能同時佔用。** 生成 2D 圖期間開 ComfyUI；進入去背或 TRELLIS 之前一定先跑 `comfy_stop.sh`。
2. **不要在 `trellis2` 環境安裝或升級任何套件。**
3. **下載或產生大檔前，先看 Windows 磁碟實際剩餘空間**（Git Bash：`df -h /c /d`）。WSL 內的 `df` 顯示的是虛擬上限，不可信。
4. **不要用會比對到自己的 `pkill -f` / `pgrep -f`**（指令字串本身含有樣式時會殺掉自己）。用 `comfy_stop.sh`，或把樣式第一個字母包成 `[x]`。
5. **不要印出 `~/.cache/huggingface/token` 的內容**，也不要列出含有該 token 的程序指令列。
6. 長時間工作（TRELLIS）用背景執行，並監看 `work/trellis.log` 的 `[done]` / `[FAIL]` 行。
7. **Blender 場景只透過 Blender MCP 建立與修改。** 不要寫 `.py` 建置腳本，也不要用 `blender -b -P` 在背景跑；所有建模、擺位、材質、燈光、渲染都由你用 `execute_blender_code` 在使用者開著的 Blender 裡一步一步做，用 `look` 看結果。MCP 連不上時停下來告訴使用者，不要改走背景 Blender。
8. ComfyUI 或 TRELLIS 佔用 GPU 期間，`look` 只用 `solid` / `wireframe` / `material` 著色，不要用 `rendered`，也不要渲染。
9. 不要在 MCP 裡呼叫 `bpy.ops.wm.read_factory_settings` 或 `read_homefile`：會重置工作階段並中斷 MCP。要清空場景就逐一刪除物件與資料。

時間參考（RTX 3060）：ComfyUI 冷啟動後第一張圖 2–5 分鐘，之後 9B 約 30 秒/張、4B 約 15 秒/張；TRELLIS 載入約 5 分鐘，每個物件 2–16 分鐘。

## 2. 流程總覽

```
步驟 1  看圖 → 寫 scene.json（物件、bbox、深度、初始 prompt）
步驟 2  01_make_refs.py → 每個物件的參考裁切圖 ref.png
步驟 3  逐物件迭代生成 2D 圖：生成 → 看圖 → 決定是否改 prompt → 再生成（至少 4 張）
步驟 4  寫 work/selection.json → 03_select_matte.py matte → 檢查去背
步驟 5  trellis_loop.sh → 用 MCP 把每個 GLB 匯入 Blender → look 逐一檢視，不合格的處理
步驟 6  用 MCP 組裝場景：相機 → 建築 → 擺放模型與招牌
步驟 6b 表面材質與散佈物 → look → 調整
步驟 6c 燈光、霧、光暈 → 渲染 → 對照原圖 → 存 scene.blend
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
- 重複出現的同款物件只列一次；其餘位置在步驟 6 用同一個模型的複本擺放。
- 大面積結構（地板、牆、樑柱、階梯、路緣、窗戶、遠景大樓）不走 img-to-3D，也不寫進 `scene.json`；它們在步驟 6 由你用 MCP 直接在 Blender 裡建模。
- 帶文字的平面物件（招牌、標示牌）設 `"mode": "image_slab"`：TRELLIS 會把文字重建成亂碼，這類物件只生成正面 2D 圖，步驟 6 在 Blender 裡做成貼圖薄板。

**scene.json 結構**。它只描述「要生成哪些物件」（步驟 1–5 的腳本會讀）；Blender 場景怎麼組裝不寫在這裡。

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
  ]
}
```

物件的可選欄位：

| 欄位 | 用途 |
|---|---|
| `ref_bbox` | 參考裁切範圍（預設是 bbox 外擴 25%）；物件很小或需要更多上下文時指定 |
| `seed_base` | 種子起點（預設 1000），想整組重來時換一個 |
| `mode: "image_slab"` | 文字招牌等平面物件：不跑 TRELLIS，步驟 6 用 2D 圖做成薄板 |
| `trellis.pipeline_type` | 預設 `"1024_cascade"`；**實心方塊類（箱子、積木、櫃子）必須設 `"512"`**，否則 VRAM 不足會 CUDA 錯誤 |
| `trellis.seed` | 3D 重做時換種子 |

`bbox` 與 `depth_m` 在步驟 6 是擺位的起點（你自己讀來用），不是由程式自動套用。

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

完成後用 MCP 把每個 `model.glb` 匯入 Blender 檢視（這些物件之後直接留在場景裡用，見步驟 6 的「匯入模型」）：

1. `execute_blender_code`：`bpy.ops.import_scene.gltf(filepath=...)`，合併成單一網格、套用變換、原點移到底部中心，物件命名為 `<id>`，
   放進 `Models_TRELLIS` 集合。先放在遠離原點的暫存位置排成一列即可。
2. `look`：`mode="angles"`、`target=["<id>"]`、`shading="material"`，至少看正面與背面斜角兩個方向。
3. 逐一對照選定的 2D 圖判斷。

| 看到的問題 | 處理方式 |
|---|---|
| 同一物件反覆 `[FAIL]`（CUDA error、device not ready） | 在 `trellis` 加 `"pipeline_type": "512"`，重跑 loop |
| 文字或字母變成亂碼 | 改成 `"mode": "image_slab"`（刪掉匯入的模型）；需要一張正面 2D 圖，沒有就回步驟 3 用正面視角重新生成 |
| 形狀明顯錯誤、破面、多出雜物 | 刪掉該物件的 `model.glb`，在 `trellis` 設不同的 `"seed"` 重跑；仍不行就回步驟 3 換一張 2D 圖 |
| 貼圖略糊但形狀正確，且是遠處小道具 | 可接受，在總結中註明 |

重做單一物件：刪除 `work/objects/<id>/model.glb` 與 Blender 裡的舊物件後重跑 `trellis_loop.sh`，再重新匯入。每個物件最多重做 2 次，之後就接受並註明。

**輸出**：每個非 `image_slab` 物件的 `work/objects/<id>/model.glb`，且都已匯入 Blender 並看過。

## 8. 步驟 6：用 Blender MCP 組裝場景

這一步沒有建置腳本。場景由你透過 Blender MCP 在使用者開著的 Blender 裡直接建出來：`execute_blender_code` 執行 `bpy` 程式碼，
`look` 看結果，`get_scene_info` 查物件的位置與尺寸。**`scene.blend` 本身就是成果，也是唯一的狀態**；要改場景就改 Blender 裡的東西，
不要把建置邏輯寫成 `.py` 檔。

**工作方式**

- 一開始先 `get_addon_status`、`get_scene_info`。場景裡若已有上一次的成果，先看清楚再決定接著做還是清掉重來（清空前要問使用者）。
- 小步進行：每次 `execute_blender_code` 只做一件事（建一個構件、擺一組物件、調一組燈），印出關鍵數值，接著 `look` 確認，再做下一件。
- 不要跨呼叫依賴 Python 變數；每段程式碼都從 `bpy.data.objects["名稱"]` 重新取得物件。需要重複用的小函式就在該段程式碼裡再寫一次。
- 物件命名要讓之後的自己（或下一個工作階段）看得懂：模型用 `scene.json` 的 `<id>`、複本用 `<id>_2`、建築用 `Pier_R1`、`Deck`、`Stairs` 這類名稱、
  燈用 `L_` 開頭。集合固定為 `Models_TRELLIS`、`Environment`、`Scatter`、`Lights_Camera`。
- 依 MCP 的規則寫程式碼：著色器節點用 `type` 找（`n.type == "BSDF_PRINCIPLED"`），不要用名稱；列舉值先讀再設。
- 每完成一個小節就存檔：`bpy.ops.wm.save_as_mainfile(filepath=r"$PW\scene.blend")`（第一次）或 `bpy.ops.wm.save_mainfile()`。
- `look` 的 `max_size` 平常用 800–1200 即可，只有要看細節時才放大。

### 6.1 相機

相機必須先定好，後面所有尺寸都靠它量。世界座標：X 向右、Y 向相機前方、Z 向上，地面 z = 0，相機在 `(0, 0, 高度)`。

1. **由原圖求相機參數。** 找兩組互相垂直的水平線（地磚縫、牆腳線、樑），各自延伸求消失點 `(u1, v1)`、`(u2, v2)`（可用 `00_grid.py` 的格線圖讀座標）。
   兩點的平均 `v` 是地平線；主點取圖片中心 `(cx, cy)`；焦距（像素）`f = sqrt(-(u1-cx)(u2-cx) - (vh-cy)²)`，`vh` 為地平線高度；
   俯仰角 `pitch = atan((cy - vh) / f)`（地平線在中心下方表示相機朝上）；水平視角 `hfov = 2·atan(cx / f)`。
   找不到兩組線時，用 `scene.json` 的 `camera.hfov_deg` 估計值，並以主角腳底落在地面來反推俯仰角。
2. 相機高度依視角判斷（站立視角約 1.5–1.7 m），這個值決定整個場景的尺度。
3. 建立 `RefCamera`：`sensor_fit='HORIZONTAL'`、`angle_x=hfov`、位置 `(0, 0, 高度)`、`rotation_euler=(π/2 + pitch, 0, 0)`；
   渲染解析度設成原圖尺寸；把原圖掛成相機背景圖（`show_background_images`，alpha 0.5），方便使用者手動比對。
4. 把求得的 `hfov_deg`、`height_m`、`pitch_deg` 寫回 `scene.json` 的 `camera` 當紀錄。

**量尺寸的方法**（之後每個構件都這樣量）：像素 `(u, v)` 的視線方向是 `((u-cx)/f, 1, -(v-cy)/f)` 再套上俯仰旋轉；
與地面 z = 0 的交點給出地面上的點；與一個已知的鉛直面（例如已定位的牆面）的交點給出該面上的高度。
先量地面上的腳點，再用腳點所在的鉛直面量高度。如果地面有規則的鋪面格線，建一個對齊格線方向的 Empty 當所有建築的父物件，
之後就能在「沿格線 / 垂直格線」的座標裡建模，數字會整齊很多。

### 6.2 建築

地面、樑柱、牆、階梯、路緣、花台、遠景大樓都在這裡建。用 `bmesh` 或 `from_pydata` 直接產生網格。

- **照原圖的結構建，不要用幾個平面交差。** 天花板若有樑、凹縫、托架就建出來；柱子有倒角、分段縫就做出來；
  花台、路緣、排水溝蓋、階梯與扶手都是實際的幾何。鋪面一塊一塊建（板與板之間留縫），每塊給一點隨機明暗。
- 植物只生成植物本體；花台、底座這類規則的東西在這裡建。
- 混凝土邊緣加 Bevel 修改器（1 段、寬 1–3 cm）；沒有倒角的邊在夜景裡吃不到光，會顯得假。
- 原圖看不到的部分（建築背面、畫面外）用簡單的方盒封住即可，但不要留下會露出天空或地平線的破口。
- 遠景：大樓用方盒加發光的窗戶 / 燈條 / 螢幕色塊就夠，重點是輪廓與亮暗位置對得上。

**每建完一組構件就比對輪廓**：`look`（`mode="camera"`、`shading="wireframe"` 或 `solid`），和原圖逐項比較邊線位置；
偏差超過幾個像素就回頭修正量測值。需要精確疊圖時，把 `look` 回傳的圖檔與原圖用 PIL 半透明疊起來看。
主要結構（地面格線、最大的柱與牆、天花板邊緣）對上之後才往下做。

### 6.3 匯入模型並擺位

模型在步驟 5 檢視時已匯入。對每個物件：

1. **整理**：合併成單一網格、套用變換、原點在底部中心；模型的正面若不是朝 -Y，先在網格資料上轉正。
2. **定位置**：用 `bbox` 底邊中點的視線與地面（或它所站的平台面：花台土面、階梯上的平台）求交點，這比 `depth_m` 可靠；
   掛在牆上的東西則與那面牆求交。`depth_m` 只在沒有可交的面時使用。
3. **定大小**：優先用真實尺寸（報箱約 1.2 m、長椅寬約 1.6 m），再回頭確認輪廓與 `bbox` 吻合；不知道真實尺寸時才用 `bbox` 高度 × 距離 ÷ f。
4. **定朝向**：對齊它旁邊的建築方向（沿路、貼牆），不要全部正對相機。
5. **重複物件**：`obj.copy()` 產生共用網格的複本，各自擺位，不要重新匯入。
6. `look`（camera）確認位置、大小、遮擋關係；再用 `mode="angles"` 從側面或上方看一次，確認沒有懸空、沒有插進地面或牆裡。
   `get_scene_info` 的 `ground` 欄位可以直接列出誰浮著。

**`image_slab` 物件**（招牌、螢幕、報紙）：依去背圖的不透明範圍建一塊薄板，正面貼 `rgba.png`（去背把面板吃掉時，改從候選圖直接裁出矩形），
側面與背面用面板底色。發光的招牌把貼圖同時接到 Emission。平放在地上的（報紙）轉 -90° 並抬高 1 cm。

**常見修正**

| 問題 | 做法 |
|---|---|
| 生成的貼圖在夜景裡太亮（紙張、白色物件像在發光） | 在 Base Color 前插一個 Multiply，乘 0.3–0.6 |
| 貼圖太暗看不出形狀 | 同上，乘 1.5–2 |
| 螢幕 / 海報的 3D 貼圖是糊的 | 從 2D 候選圖把該面用 PIL 攤平成矩形，做成一片貼在模型正面前方幾毫米的發光平面，設為模型的子物件 |
| 同款物件看起來一模一樣 | 複本各自轉一點角度；海報類換色調或換圖 |

### 步驟 6b：表面材質與散佈物

**材質**用程序化節點直接做，座標用世界空間位置（Geometry → Position），這樣紋理尺度就是真實的公尺，不需要 UV。

- 混凝土 / 地面：大尺度噪波做明暗，再乘一層更大尺度的噪波當髒污（髒的地方粗糙度高、乾淨的地方較亮滑），細噪波接 Bump。
- 老舊牆柱：沿 Z 拉長的噪波做水漬條紋。對比不要太大，先看原圖是乾淨還是斑駁。
- 金屬、塗裝、玻璃：Principled BSDF 調 Metallic / Roughness 即可。
- 程序化做不出來的質感才用 Poly Haven（CC0）：`python pipeline/polyhaven.py search|size|get`（下載到 `work/textures/<id>/`），
  下載前先告訴使用者要下載什麼、來源、大小，預設 1k；在 Blender 裡用 Box 投影的 Image Texture 接上。只用 Poly Haven，其他來源授權不一。
- 顏色以原圖為準。夜景的表面反照率通常比直覺暗很多（地面 0.1 左右），先調材質再調燈，不要用燈去補過亮的材質。

**散佈物**：原圖地上有什麼才撒什麼（紙張、碎石、落葉）。做 3–5 個原型網格，其餘用共用網格的複本，隨機位置、旋轉、大小；
只撒在原圖有東西的區域，避開已放好的物件（用各物件的包圍盒檢查），全場景不超過 500 個。原圖沒有的東西不要加。

`look`（camera、`rendered`）對照原圖檢查，最多調 3 輪。**通過條件**：表面看得出材質且尺度合理；散佈物沒有穿過任何物件；色調沒有偏離原圖。

| 看到的問題 | 調整 |
|---|---|
| 紋理顆粒大得像巨石，或小到變成雜訊 | 改 Mapping 的 Scale（對照場景裡物件的真實大小） |
| 色調和原圖不合 | 改基色 |
| 地面太亮或反光太強 | 調暗基色、提高粗糙度 |
| 雜物太搶眼、太整齊或太稀疏 | 改數量、大小、顏色、隨機種子 |

### 步驟 6c：燈光、渲染、存檔

確定 ComfyUI 與 TRELLIS 都已停止。引擎用 EEVEE（開 raytracing），色彩管理 AgX。

**燈光**：先看原圖列出光源與它們照亮了什麼，再一盞一盞加，每加一盞就 `look`（`rendered`）。

- 每個看得到的光源（招牌、螢幕、路燈、店面）給一盞對應的燈，顏色取自原圖；發光物件本身用 Emission。
- 主光要有方向。只有均勻的補光會讓場景發平；先把主光與最暗處定下來，補光最後才加，而且要弱。
- World 用接近原圖天空的顏色；夜景幾乎是黑的。
- 霧：遠景有霧氣時加一個 Volume Scatter 方盒，密度從 0.001 起。
- 光暈：霓虹與螢幕周圍的 glow 用合成器的 Glare 節點（Type 設 Bloom）。`look` 的 `rendered` 看不到合成器效果，要真的渲染一次才看得到。
- EEVEE 回報 shadow buffer 已滿時，把小的點綴燈關掉陰影，或調 `scene.eevee.shadow_pool_size`。

**渲染與存檔**

```python
# 透過 execute_blender_code 執行
scene.camera = bpy.data.objects["RefCamera"];      scene.render.filepath = r"$PW\work\renders\view_ref.png";      bpy.ops.render.render(write_still=True)
scene.camera = bpy.data.objects["OverviewCamera"]; scene.render.filepath = r"$PW\work\renders\view_overview.png"; bpy.ops.render.render(write_still=True)
scene.camera = bpy.data.objects["RefCamera"]
bpy.ops.file.pack_all(); bpy.ops.wm.save_mainfile()
```

`OverviewCamera` 放在場景斜上方、看得到全景的位置，用來檢查懸空與穿插。Read 兩張渲染圖，再產生左右對照圖並 Read：

```bash
wsl -e bash -lc 'source ~/miniconda3/etc/profile.d/conda.sh; conda activate trellis2; python $P/pipeline/07_compare.py $P'
# → work/renders/compare.png
```

對照原圖逐項檢查，回到 Blender 修正，最多 3 輪：整體亮度與色調、每個光源的位置與顏色、主要物件的位置與大小、材質的明暗。

**通過條件**：主要物件與建築輪廓的位置、大小、遮擋關係與原圖一致；沒有物件懸空或穿插；文字可讀；整體明暗與色調接近原圖；
`scene.blend` 已存檔且貼圖已打包（沒有未打包的外部圖檔）。

## 9. 步驟 7：總結回報

交付時說明：

- 物件表：每個物件的做法（TRELLIS / 512 / image_slab / 在 Blender 建模）、生成了幾張 2D 圖、prompt 改了幾次、最終品質。
- 建築：建了哪些結構、尺寸怎麼量出來的。
- 表面材質與散佈物：各表面是程序化節點還是 Poly Haven 素材（附 asset id）、撒了什麼、哪些是原圖看不清而推測的。
- 不合格或妥協的項目與原因。
- 與原圖的已知差異（深度是估計值、被遮擋處是模型補的）。
- 各階段耗時（可從 `work/generation_log.jsonl` 和 `work/trellis.log` 取得）。
- 確認 ComfyUI 與 TRELLIS 都已停止。

只回報實際看過、驗證過的結果；沒檢查的項目要明說沒檢查。

## 10. 檔案對照

```
scene.json                      物件定義（你撰寫與修改）
pipeline/00_grid.py             座標格線圖
pipeline/01_make_refs.py        參考裁切圖
pipeline/02_generate.py         FLUX.2 生成（--next 迭代模式 / 批次模式）
pipeline/comfy_edit.py          ComfyUI API 客戶端（被 02 呼叫）
pipeline/comfy_start.sh|stop.sh 啟動 / 停止 ComfyUI
pipeline/03_select_matte.py     對照表（sheet）與去背（matte）
pipeline/04_trellis_batch.py    TRELLIS.2 批次（被 loop 呼叫）
pipeline/trellis_loop.sh        TRELLIS 自動重啟迴圈
pipeline/polyhaven.py           搜尋 / 下載 Poly Haven CC0 貼圖
pipeline/07_compare.py          原圖與渲染的對照圖
work/objects/<id>/              ref.png、cand_*.png、prompts.json、sheet.png、rgba.png、review.png、model.glb
work/selection.json             選圖結果與理由（你撰寫）
work/textures/<asset id>/       下載的 Poly Haven 貼圖
work/renders/                   渲染圖與對照圖
scene.blend                     場景本身：由你透過 Blender MCP 建立，沒有對應的建置腳本
```
