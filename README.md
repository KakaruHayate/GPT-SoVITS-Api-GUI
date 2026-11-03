# GPT-SoVITS-Api-GUI (gsv.cpp fork)

GAG is an inference GUI for GPT-SoVITS. This fork drives **[gsv.cpp](https://github.com/KakaruHayate/gsv.cpp)**
instead of the upstream Python API: the API tab launches `gsv_server` (and, when configured, the
Python text sidecar), and the rest of the GUI talks to the same api_v2-compatible HTTP service.

## 仅支持 GPT-SoVITS V5（gsv.cpp 改版）

本改版只面向 **gsv.cpp** 的 V5 模型：5turbo（4 步）和 5dev（32 步 + cache-dit）。
界面按引擎实际支持的参数裁剪，不再有 v1/v2/v3/v4 的 ckpt/pth 模型选择、并行推理、数据分桶、
音频超分、辅助参考音频等入口。可用参数：音色（voice）、采样步数、cache-dit（跳步缓存）、
跳过首步（skip_first_step）、top_k/top_p/temperature、语速、种子、重复惩罚、文本分割方式。

参考音频必须 3-10 秒且位于 API 管理页允许的目录内；参考文本必填（V5 不支持无参考文本模式）。

## Running with gsv.cpp

1. Put the GUI next to a gsv.cpp runtime, e.g.

   ```
   GAG/
   runtime/
     gsv/gsv_server.exe        # gsv.cpp release / one-click package
     models/                   # GGUF models + frontend/ data
     python/python.exe         # optional: embedded Python for the text sidecar
     tools/text_sidecar.py     # text frontend script (ships with gsv.cpp)
   ```

2. Start GAG and open the **API** tab. Set:
   - `gsv_server`: path to `gsv_server.exe`
   - `Models`: the model directory
   - `Device`: `vulkan` or `cpu`
   - `Sidecar Python`: path to `python.exe` (optional; the GUI starts `tools/text_sidecar.py`
     automatically when set)
   - `Frontend data`: directory containing `g2pw/`, `bert_tokenizer/`, `nltk_data/`
   - `Reference dirs`: directories the engine may read reference audio from (`;`-separated)
3. Press **Start**. The log shows the sidecar and engine output; the TTS tabs then use
   `http://127.0.0.1:9880`.

## Introduction

<p float="left">
  <img src="GAG_tools/images/GAG-1.png" width="32%" />
  <img src="GAG_tools/images/GAG-2.png" width="32%" />
  <img src="GAG_tools/images/GAG-3.png" width="32%" />
</p>

**注意： 如果你的系统语言为中文，那么界面语言应该会自动切换为中文，你也可以手动修改GAG_config.json中的language为en或zh来指定界面语言**


## Usage

Download the release and extract it, then run `GAG.exe` (or `python gsv_api_gui.py` from a dev
checkout). The GUI starts the gsv.cpp backend from the API tab, or automatically when
`autostart_api` is enabled in `GAG_config.json`.

## Installation

1. Install FFmpeg and ensure it's in your system PATH.
2. Clone this repository:
   ```
   git clone https://github.com/AliceNavigator/GPT-SoVITS-Api-GUI.git
   ```
3. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
4. Execute:
   ```
   python gsv_api_gui.py
   ```
