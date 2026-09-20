# 图像工坊 · 批量压缩（image-studio）

本地网页版图片工具，**三个 tab**：批量压缩 / 读图参数 / 一键分类（都支持批量），用法参考 [squoosh.app](https://squoosh.app/)：
左边拖图 / 右边调参数 / 实时看体积变化 / 前后滑动对比，最后一键打包下载。

- **零第三方服务**：本地跑，图不出机器。
- **只依赖 Pillow**（本机已装，含 libjpeg-turbo / zlib / libwebp / libavif —— JPEG、PNG、WebP、AVIF 四种编码器全在）。
- **批量能力**：多选、拖整个文件夹、或让服务端**直接扫描目录**（几万张不用手动选）；多线程并行；结果打包 ZIP。

---

## 一、启动

```bat
双击 run.bat
```

首次运行会自动建 `.venv`（`py -3.13 -m venv .venv --system-site-packages`，这样直接用系统里已装的 Pillow，**不需要联网装包**）。
启动后浏览器自动打开 `http://127.0.0.1:8720`（端口被占用会自动往后找）。

命令行用法：

```bat
.venv\Scripts\python.exe run_server.py                 :: 默认 8720
.venv\Scripts\python.exe run_server.py --port 8721
.venv\Scripts\python.exe run_server.py --no-browser
```

## 二、四种批量用法

| 用法 | 适合 | 操作 |
| --- | --- | --- |
| **选择文件夹** | 一个目录里的图（含子文件夹） | 点「选择文件夹」选目录，浏览器会把里面所有图片一次性读进来后上传 |
| **选择文件** | 少量图 | 点「选择文件」多选 |
| **拖拽** | 几十张、临时挑的图 | 把文件**或整个文件夹**拖进虚线框 |
| **扫描目录**（推荐大批量） | 几千到几万张 | 填目录 → 勾「含子文件夹」→「扫描该目录」。**不上传、不走网络**，服务端直接读盘 |

> 「可扫描目录」只影响**界面预览/对比**能不能读到文件；填在设置里更规范（设置 → 可扫描目录）。

## 三、参数说明

| 参数 | 说明 |
| --- | --- |
| **预设** | `网页分享`（WebP Q75 + 长边 1920）／`最大压缩`（WebP Q55 + 长边 1600）／`接近无损`（保持格式 Q92）／`保持原格式` |
| **目标格式** | 保持原格式 / WebP（同画质体积最小）/ JPEG / PNG / AVIF（最省，编码慢） |
| **压缩方式** | `按质量`：直接用 0–100 滑块；`按体积`：**二分搜索**质量，必要时按 85% 逐步缩分辨率，压到每张不超过目标 KB 为止 |
| **缩放** | 不缩放 / 按长边 / 按百分比 / 放进指定框（等比不变形）；**尺寸对齐**可把宽高取整到 2/8/16/32/64 的倍数（方便再送模型） |
| **PNG 调色板量化** | 不量化（无损）／256/128/64/32 色。色块类图能省一大截；**照片类慎用** |
| **无损模式** | WebP / AVIF 走无损编码；PNG 走最高压缩级别 |
| **保留 EXIF/ICC** | 默认**关**：剥掉元数据能再省几 KB，同时抹掉 GPS/机型等隐私信息 |
| **不比原图小就保留原文件** | 默认开。已经压过头的图不会被"越压越大"，直接沿用原文件并在表格里标注 |
| **并行线程** | 默认 4。AVIF 慢，可调到 8；机械盘别开太高 |

### 前后对比

表格任意一行点「对比」（或点缩略图）：
- **滑动对比**：拖动中间竖线左右分屏
- **并排对比**：左右各一张，看细节
- 顶部显示：原体积 → 压缩后体积、节省百分比、压缩后尺寸、实际使用的质量值

## 四、读图参数（独立 tab，支持批量）

顶栏第二个 tab「🔍 读图参数」：**看一张图（或一整批图）是用什么模型、什么 LoRA、什么提示词跑出来的**。入口全部支持批量：

| 入口 | 适合 |
| --- | --- |
| 拖拽多张图 / 整个文件夹 | 几十张 |
| 「选择图片」多选 / 「选择文件夹」 | 一个目录（含子文件夹） |
| 填目录 → 「读取该目录」 | 大量图，**服务端直接读盘、不上传** |
| 压缩 tab 里每行的「参数」按钮 | 顺手看刚压的那张（自动跳到本 tab 并展开详情） |

**结果页**：一张图一行（预览 / 文件名 / 尺寸 / 工具 / 模型 / LoRA / 正向提示词摘要），顶部汇总「N 张里 M 张带生成参数 · 涉及模型 X 个 · LoRA 引用 Y 次」；点任意一行看完整详情 —— **左边大图预览（可切「适应窗口 / 原始大小」，也能在新窗口打开原图），右边是参数**：

| 输出 | 内容 |
| --- | --- |
| **模型** | Checkpoint / UNet / VAE / CLIP·文本编码器 / ControlNet / 放大模型 / IPAdapter… |
| **LoRA** | 名称 + `model` 权重 + `clip` 权重（按加载链顺序） |
| **提示词** | 正向 / 负向各一栏，一键复制；自动提取 `embedding:xxx` |
| **采样参数** | seed / steps / cfg / sampler / scheduler / denoise / 宽高（A1111 另有 clip skip 等） |
| **其它** | 节点构成、元数据来源、原始 JSON 可展开 |

**批量动作**：「📋 复制所有正向提示词」（按文件名分组）／「⬇ 导出全部报告（Markdown，一张一节）」。

**认得这几种写法**：

1. **ComfyUI**：PNG 的 `prompt`/`workflow` tEXt 块；JPEG/WebP 的 EXIF UserComment；XMP；以及**字节兜底**（容器读不到时直接在文件里搜工作流 JSON，按「含 `class_type` 最多的对象」取，避免只截到一个子节点）。
2. **A1111 / Forge / SD.Next**：`parameters` 文本，包括 `<lora:名字:权重>` 与 `Lora hashes:`。
3. 提示词**不是直接从节点抄的**，而是顺 conditioning 链回溯：`KSampler.positive/negative` → 透传节点（ControlNetApplyAdvanced / ConditioningCombine / SetArea / Concat…）→ `CLIPTextEncode`，并按**输出槽位**区分正负，保证正负不串味。

> 没有元数据的图（截图、手绘、被平台抹过 EXIF 的图）会明确标「未检测到生成参数」，不会瞎猜。

## 五、一键分类（第三个 tab）

**按「有没有 ComfyUI 生成信息」把一堆图自动分到不同文件夹** —— 整理下载目录、挑出没元数据的图，一步到位。

| 步骤 | 说明 |
| --- | --- |
| 1. 填目录 | 支持子文件夹；**服务端直接扫描，不上传** |
| 2. 选规则 | ① **按 ComfyUI 信息**（A=含 ComfyUI，B=其余）② 按有无生成信息（跑过的/纯图）③ 三分类（ComfyUI / 其他工具 / 无信息） |
| 3. 看预览 | 每张图归到哪一类、判定依据（ComfyUI tEXt prompt / EXIF / A1111 parameters…）、体积、尺寸；点一行看**左大图 + 右判定详情** |
| 4. 点「🚀 开始分类」 | 复制到 `输出目录/文件夹A` 与 `输出目录/文件夹B`（默认 `output/分类_时间戳/`） |

**几个安全设计**：

* 默认**复制**，原文件不动；要移动必须选「移动」并在弹窗里确认。
* 先扫描出清单、**确认后才动文件**；扫描时自动跳过目标目录，不会把产物又搬进产物。
* 目标目录 = 来源目录会被拒绝；同名文件可选 自动改名 / 跳过 / 覆盖。
* 可选「保留来源子目录结构」（默认开），避免不同子目录的同名图互相撞。
* 可「⬇ 导出分类清单（CSV）」，带 Excel BOM，双击直接打开不乱码。

## 六、输出

- 默认输出到项目下的 `output\压缩_年月日-时分秒\`，文件名沿用原名、扩展名随目标格式（`photo.jpg` → `photo.webp`）。
- 输出目录可在**设置**里改（比如指到你的归档盘）。
- 「⬇ 下载全部（ZIP）」把这一批打包下载；「📂 打开输出目录」直接在资源管理器里打开。

## 七、设置（`config.json`）

设置页保存到项目根目录的 `config.json`（首次运行由 `config.example.json` 生成）。

```jsonc
{
  "host": "127.0.0.1",
  "port": 8720,
  "workers": 4,                       // 并行线程
  "input_roots": [],                  // 界面里允许读取/预览的目录（留空则只允许项目内目录）
  "output_dir": "",                   // 留空 = <项目>\output
  "exts": ["jpg","jpeg","png","webp","avif","bmp","tif","tiff","gif"],
  "defaults": { "format": "keep", "quality": 78, "mode": "quality", "target_kb": 200,
                "lossless": false, "resize": "none", "long_edge": 1920, "percent": 100,
                "box_w": 1920, "box_h": 1080, "align": 0,
                "keep_metadata": false, "png_colors": 0, "skip_if_larger": true }
}
```

> **注意**：`config.json` 里会带你的本机路径，`.gitignore` 已经排除它，分享/推仓库时只带 `config.example.json`。

## 八、HTTP 接口（想脚本化调用可以直接用）

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| GET | `/api/config` · POST `/api/config` | 读/写配置 |
| POST | `/api/scan` `{dir, recursive}` | 扫描目录返回图片清单（含尺寸/体积） |
| POST | `/api/upload` | multipart 上传（网页拖拽走这里） |
| POST | `/api/preview` `{path, settings}` | 单张试压（实时估算体积用） |
| POST | `/api/compress` `{files[], settings, workers}` | 启动批量任务，返回 `job.id` |
| GET | `/api/job?id=` | 任务进度 + 每张结果（原/新体积、节省%、耗时） |
| POST | `/api/cancel` `{id}` | 停止任务 |
| GET | `/api/zip?id=` | 打包下载 |
| GET | `/api/thumb?path=&w=` | 缩略图（界面用） |
| POST | `/api/meta` `{path}` 或 `{paths:[…]}` | 读图片生成参数（单张 / 批量，批量返回 `metas[]`） |
| POST | `/api/classify/scan` `{dir,recursive,rule,out_dir}` | 扫描目录并判定归类（只读，返回清单） |
| POST | `/api/classify/run` `{items,root,out_dir,names,mode,conflict,preserve_tree,confirm}` | 执行分类（复制/移动） |
| POST | `/api/reveal` `{path}` | 在资源管理器里打开目录 |

## 九、目录结构

```
image-studio/
├─ app/
│  ├─ __init__.py      配置读写（路径全部可配，代码里不写死本机路径）
│  ├─ imaging.py       压缩核心：加载 / 缩放 / 编码 / 二分到目标体积 / 元数据
│  ├─ metadata.py      读图参数：ComfyUI 工作流 / A1111 parameters 解析
│  ├─ jobs.py          批量引擎：扫描目录、线程池、进度快照、打包 ZIP
│  └─ server.py        HTTP 接口（标准库 http.server）
├─ static/             前端（原生 HTML/CSS/JS，无框架）
├─ run_server.py       启动入口
├─ run.bat             双击启动
├─ config.example.json 配置模板（会被复制成 config.json）
└─ output/             默认输出目录
```

## 十、常见问题

- **压完反而更大？** 勾着「不比原图小就保留原文件」，表格里会显示「已保留原文件」。想强制重编码就取消勾选。
- **线条图/截图转 WebP 变大了？** 正常现象：这类图 PNG 已经很小，WebP 的低频压缩反而不划算。表格里会标一个黄色「变大了」标签 —— 这种图保持原格式（PNG）或者把 PNG 调色板量化调到 256 色更合适。
- **PNG 压不动？** PNG 是无损格式。要么把「PNG 调色板量化」设成 256/128 色（色块图省得多），要么直接选 WebP。
- **透明背景变白？** 输出 JPEG/BMP 不支持透明通道，会统一垫白底（不会变黑）。
- **AVIF 很慢？** 正常，同画质它最小。批量时把并行线程调到 8 会快很多。
- **想压视频/PDF？** 这个工具只管图片；视频可以另开一个功能模块（说一声就加）。
