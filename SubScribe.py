# -*- coding: utf-8 -*-
"""
SRT字幕拆分 & AI处理工具 - GUI版本
"""

import os
import re
import json
import sys
import threading
from pathlib import Path
from dataclasses import dataclass
from typing import List
from datetime import datetime

import customtkinter as ctk
from tkinter import filedialog, messagebox
import httpx

# 尝试导入拖拽支持库 (Windows)
try:
    import windnd
    HAS_WINDND = True
except ImportError:
    HAS_WINDND = False


def get_app_path():
    """获取应用程序所在目录（兼容PyInstaller打包）"""
    if getattr(sys, 'frozen', False):
        # 打包后的exe运行
        return os.path.dirname(sys.executable)
    else:
        # 直接运行py文件
        return os.path.dirname(os.path.abspath(__file__))


# ============== 数据类 ==============

@dataclass
class Subtitle:
    """字幕条目"""
    index: int
    start_time: float
    end_time: float
    text: str


# ============== 核心功能函数 ==============

def parse_time(time_str: str) -> float:
    """解析SRT时间格式为秒数"""
    time_str = time_str.replace(',', '.')
    parts = time_str.split(':')
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


def format_time_for_display(seconds: float) -> str:
    """将秒数格式化为显示格式"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_srt(file_path: str) -> List[Subtitle]:
    """解析SRT文件"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    blocks = re.split(r'\n\s*\n', content.strip())
    subtitles = []
    
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 3:
            continue
        
        try:
            index = int(lines[0].strip())
            time_line = lines[1].strip()
            time_match = re.match(r'(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})', time_line)
            if not time_match:
                continue
            
            start_time = parse_time(time_match.group(1))
            end_time = parse_time(time_match.group(2))
            text = '\n'.join(lines[2:])
            
            subtitles.append(Subtitle(index=index, start_time=start_time, end_time=end_time, text=text))
        except (ValueError, IndexError):
            continue
    
    return subtitles


def get_subtitles_in_range(subtitles: List[Subtitle], start_sec: float, end_sec: float) -> List[Subtitle]:
    """获取指定时间范围内的字幕"""
    return [s for s in subtitles if start_sec <= s.start_time < end_sec]


def subtitles_to_markdown(subtitles: List[Subtitle]) -> str:
    """将字幕列表转换为Markdown格式"""
    lines = []
    for sub in subtitles:
        time_display = format_time_for_display(sub.start_time)
        lines.append(f"**[{time_display}]** {sub.text}")
        lines.append("")
    return '\n'.join(lines)


def find_part_files(directory: str, base_name: str) -> List[str]:
    """查找指定目录下的Part文件并按序号排序"""
    pattern = re.compile(rf'^{re.escape(base_name)}-Part(\d+)\.md$')
    files = []
    
    for filename in os.listdir(directory):
        match = pattern.match(filename)
        if match:
            part_num = int(match.group(1))
            files.append((part_num, os.path.join(directory, filename)))
    
    files.sort(key=lambda x: x[0])
    return [f[1] for f in files]


# ============== 支持拖拽的Entry组件 ==============

class DropEntry(ctk.CTkEntry):
    """支持拖拽文件的输入框"""
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        
        # 绑定粘贴事件处理路径
        self.bind('<Control-v>', self.on_paste)
        self.bind('<Button-1>', self.on_click)
        
    def on_paste(self, event):
        """处理粘贴，自动清理路径"""
        try:
            clipboard = self.clipboard_get()
            # 清理路径（去掉引号和多余空格）
            cleaned = clipboard.strip().strip('"').strip("'")
            if cleaned:
                self.delete(0, 'end')
                self.insert(0, cleaned)
            return 'break'
        except:
            pass
    
    def on_click(self, event):
        """点击时全选"""
        self.after(50, lambda: self.select_range(0, 'end'))


# ============== GUI 应用 ==============

class SRTSplitterApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        # 窗口设置
        self.title("SRT 字幕拆分 & AI处理工具")
        self.geometry("950x750")
        self.minsize(850, 650)
        
        # 主题设置
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        
        # 变量
        self.srt_path = ctk.StringVar()
        self.split_duration = ctk.StringVar(value="30")
        self.overlap_duration = ctk.StringVar(value="1")
        
        # API相关变量
        self.api_keys = {}
        
        # 加载配置
        self.load_config()
        
        # 创建界面
        self.create_widgets()
        
        # 设置拖拽支持
        self.setup_drag_drop()
        
        # 处理状态
        self.is_processing = False
    
    def load_config(self):
        """加载配置文件"""
        config_path = os.path.join(get_app_path(), 'config.json')
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.api_keys = config.get('api_keys', {})
                    # 兼容旧格式
                    if 'deepseek_api_key' in config and 'deepseek' not in self.api_keys:
                        self.api_keys['deepseek'] = config['deepseek_api_key']
                    # 加载提示词
                    self.saved_prompt = config.get('prompt', '')
                    # 加载保存的URL
                    self.saved_deepseek_url = self.api_keys.get('deepseek_url', 'https://api.deepseek.com/chat/completions')
            except:
                self.saved_prompt = ''
                self.saved_deepseek_url = 'https://api.deepseek.com/chat/completions'
        else:
            self.saved_prompt = ''
            self.saved_deepseek_url = 'https://api.deepseek.com/chat/completions'
    
    def save_config(self):
        """保存配置"""
        config_path = os.path.join(get_app_path(), 'config.json')
        
        # 获取当前API密钥和模型
        self.api_keys['deepseek'] = self.deepseek_key_entry.get().strip()
        self.api_keys['deepseek_model'] = self.deepseek_model_var.get()
        self.api_keys['deepseek_url'] = self.deepseek_url_entry.get().strip()
        
        config = {
            'api_keys': self.api_keys,
            'prompt': self.prompt_text.get("1.0", "end-1c"),
            # 保持旧格式兼容
            'deepseek_api_key': self.api_keys.get('deepseek', '')
        }
        
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        
        self.log("✅ 配置已保存")
    
    def create_widgets(self):
        """创建界面组件"""
        # 主容器
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        
        # ===== 标签页容器 =====
        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=0, column=0, padx=15, pady=15, sticky="nsew")
        
        # 创建标签页
        self.tab_main = self.tabview.add("🏠 主界面")
        self.tab_prompt = self.tabview.add("📝 提示词")
        self.tab_api = self.tabview.add("🔑 API设置")
        
        # 设置各标签页
        self.setup_main_tab()
        self.setup_prompt_tab()
        self.setup_api_tab()
    
    def setup_main_tab(self):
        """设置主界面标签页"""
        self.tab_main.grid_columnconfigure(0, weight=1)
        self.tab_main.grid_rowconfigure(1, weight=1)
        
        # ===== 文件和参数区域 =====
        top_frame = ctk.CTkFrame(self.tab_main)
        top_frame.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        top_frame.grid_columnconfigure(1, weight=1)
        
        # SRT文件（支持拖拽）
        ctk.CTkLabel(top_frame, text="📄 SRT文件:", font=("Microsoft YaHei", 13)).grid(row=0, column=0, padx=10, pady=12, sticky="w")
        
        srt_frame = ctk.CTkFrame(top_frame, fg_color="transparent")
        srt_frame.grid(row=0, column=1, padx=5, pady=12, sticky="ew")
        srt_frame.grid_columnconfigure(0, weight=1)
        
        self.srt_entry = DropEntry(srt_frame, textvariable=self.srt_path, font=("Microsoft YaHei", 12),
                                    placeholder_text="拖拽文件到此处，或点击右侧浏览按钮选择...")
        self.srt_entry.grid(row=0, column=0, padx=(0, 10), sticky="ew")
        
        ctk.CTkButton(top_frame, text="浏览", width=80, command=self.browse_srt).grid(row=0, column=2, padx=10, pady=12)
        
        # 参数行
        param_frame = ctk.CTkFrame(top_frame, fg_color="transparent")
        param_frame.grid(row=1, column=0, columnspan=3, padx=5, pady=12, sticky="ew")
        
        ctk.CTkLabel(param_frame, text="✂️ 拆分时长(分钟):", font=("Microsoft YaHei", 13)).pack(side="left", padx=(5, 5))
        ctk.CTkEntry(param_frame, textvariable=self.split_duration, width=80, font=("Microsoft YaHei", 12)).pack(side="left", padx=(0, 20))
        
        ctk.CTkLabel(param_frame, text="🔄 重叠时长(分钟):", font=("Microsoft YaHei", 13)).pack(side="left", padx=(0, 5))
        ctk.CTkEntry(param_frame, textvariable=self.overlap_duration, width=80, font=("Microsoft YaHei", 12)).pack(side="left", padx=(0, 20))
        
        # ===== 日志输出区域 =====
        log_frame = ctk.CTkFrame(self.tab_main)
        log_frame.grid(row=1, column=0, padx=10, pady=(5, 10), sticky="nsew")
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(0, weight=1)
        
        self.log_text = ctk.CTkTextbox(log_frame, font=("Consolas", 12), wrap="word")
        self.log_text.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        
        # ===== 按钮区域 =====
        btn_frame = ctk.CTkFrame(self.tab_main)
        btn_frame.grid(row=2, column=0, padx=10, pady=(5, 10), sticky="ew")
        btn_frame.grid_columnconfigure((0, 1), weight=1)
        
        self.btn_run = ctk.CTkButton(btn_frame, text="🚀 开始处理", font=("Microsoft YaHei", 14, "bold"),
                                      height=50, fg_color="#28a745", hover_color="#218838", command=self.run_full_pipeline)
        self.btn_run.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        
        self.btn_clear = ctk.CTkButton(btn_frame, text="🗑️ 清空日志", font=("Microsoft YaHei", 14, "bold"),
                                        height=50, fg_color="#6c757d", hover_color="#5a6268", command=self.clear_log)
        self.btn_clear.grid(row=0, column=1, padx=10, pady=10, sticky="ew")
        
        # 初始日志
        self.log("欢迎使用 SRT 字幕拆分 & AI处理工具！")
        self.log("=" * 50)
        self.log("💡 提示：")
        self.log("   1. 可直接将SRT文件拖拽到输入框")
        self.log("   2. 请先在「提示词」标签页设置AI处理指令")
        self.log("   3. 请先在「API设置」标签页配置API密钥")
        self.log("=" * 50)
    
    def setup_prompt_tab(self):
        """设置提示词标签页"""
        self.tab_prompt.grid_columnconfigure(0, weight=1)
        self.tab_prompt.grid_rowconfigure(1, weight=1)
        
        # 说明
        hint_label = ctk.CTkLabel(self.tab_prompt, 
                                   text="在下方输入提示词，AI将按照此提示词处理每一段字幕内容：",
                                   font=("Microsoft YaHei", 13))
        hint_label.grid(row=0, column=0, padx=15, pady=(15, 10), sticky="w")
        
        # 提示词输入框
        self.prompt_text = ctk.CTkTextbox(self.tab_prompt, font=("Microsoft YaHei", 13), wrap="word")
        self.prompt_text.grid(row=1, column=0, padx=15, pady=(0, 10), sticky="nsew")
        
        # 加载已保存的提示词
        if hasattr(self, 'saved_prompt') and self.saved_prompt:
            self.prompt_text.insert("1.0", self.saved_prompt)
        
        # 按钮
        btn_frame = ctk.CTkFrame(self.tab_prompt, fg_color="transparent")
        btn_frame.grid(row=2, column=0, padx=15, pady=(0, 15), sticky="ew")
        
        ctk.CTkButton(btn_frame, text="💾 保存提示词", font=("Microsoft YaHei", 13),
                      command=self.save_config).pack(side="right", padx=5)
        
        ctk.CTkButton(btn_frame, text="🗑️ 清空", font=("Microsoft YaHei", 13),
                      fg_color="#dc3545", hover_color="#c82333",
                      command=lambda: self.prompt_text.delete("1.0", "end")).pack(side="right", padx=5)
    
    def setup_api_tab(self):
        """设置API标签页"""
        self.tab_api.grid_columnconfigure(0, weight=1)
        
        # DeepSeek API
        deepseek_frame = ctk.CTkFrame(self.tab_api)
        deepseek_frame.grid(row=0, column=0, padx=15, pady=15, sticky="ew")
        deepseek_frame.grid_columnconfigure(1, weight=1)
        
        ctk.CTkLabel(deepseek_frame, text="DeepSeek API", font=("Microsoft YaHei", 15, "bold")).grid(row=0, column=0, columnspan=3, padx=15, pady=(15, 10), sticky="w")
        
        # API密钥
        ctk.CTkLabel(deepseek_frame, text="API密钥:", font=("Microsoft YaHei", 13)).grid(row=1, column=0, padx=15, pady=10, sticky="w")
        self.deepseek_key_entry = ctk.CTkEntry(deepseek_frame, font=("Microsoft YaHei", 12), show="•")
        self.deepseek_key_entry.grid(row=1, column=1, padx=(5, 10), pady=10, sticky="ew")
        
        # 加载已保存的密钥
        if self.api_keys.get('deepseek'):
            self.deepseek_key_entry.insert(0, self.api_keys['deepseek'])
        
        # 测试按钮
        self.btn_test_api = ctk.CTkButton(deepseek_frame, text="🔗 测试连接", width=100,
                                           font=("Microsoft YaHei", 12), command=self.test_deepseek_api)
        self.btn_test_api.grid(row=1, column=2, padx=(5, 15), pady=10)
        
        # 接口地址
        ctk.CTkLabel(deepseek_frame, text="接口地址:", font=("Microsoft YaHei", 13)).grid(row=2, column=0, padx=15, pady=10, sticky="w")
        self.deepseek_url_entry = ctk.CTkEntry(deepseek_frame, font=("Microsoft YaHei", 12))
        self.deepseek_url_entry.grid(row=2, column=1, columnspan=2, padx=(5, 15), pady=10, sticky="ew")
        self.deepseek_url_entry.insert(0, getattr(self, 'saved_deepseek_url', 'https://api.deepseek.com/chat/completions'))
        
        # 模型选择
        ctk.CTkLabel(deepseek_frame, text="选择模型:", font=("Microsoft YaHei", 13)).grid(row=3, column=0, padx=15, pady=10, sticky="w")
        
        self.deepseek_models = [
            "deepseek-chat",
            "deepseek-coder", 
            "deepseek-reasoner"
        ]
        self.deepseek_model_var = ctk.StringVar(value=self.api_keys.get('deepseek_model', 'deepseek-chat'))
        self.deepseek_model_combo = ctk.CTkComboBox(deepseek_frame, values=self.deepseek_models,
                                                     variable=self.deepseek_model_var,
                                                     font=("Microsoft YaHei", 12), width=200)
        self.deepseek_model_combo.grid(row=3, column=1, padx=(5, 10), pady=10, sticky="w")
        
        # 刷新模型列表按钮
        self.btn_refresh_models = ctk.CTkButton(deepseek_frame, text="🔄 获取模型", width=100,
                                                 font=("Microsoft YaHei", 12), command=self.fetch_deepseek_models)
        self.btn_refresh_models.grid(row=3, column=2, padx=(5, 15), pady=10)
        
        # 测试结果显示
        self.api_status_label = ctk.CTkLabel(deepseek_frame, text="", font=("Microsoft YaHei", 12))
        self.api_status_label.grid(row=4, column=0, columnspan=3, padx=15, pady=(5, 15), sticky="w")
        
        # 预留更多API接口位置
        placeholder_frame = ctk.CTkFrame(self.tab_api)
        placeholder_frame.grid(row=1, column=0, padx=15, pady=10, sticky="ew")
        
        ctk.CTkLabel(placeholder_frame, text="🔮 更多API接口即将支持...", 
                     font=("Microsoft YaHei", 13), text_color="gray").pack(padx=15, pady=20)
        
        # 保存按钮
        ctk.CTkButton(self.tab_api, text="💾 保存设置", font=("Microsoft YaHei", 13, "bold"),
                      height=40, command=self.save_config).grid(row=2, column=0, padx=15, pady=15, sticky="e")
    
    def test_deepseek_api(self):
        """测试DeepSeek API连接"""
        api_key = self.deepseek_key_entry.get().strip()
        if not api_key:
            self.api_status_label.configure(text="❌ 请先输入API密钥", text_color="red")
            return
        
        self.btn_test_api.configure(state="disabled", text="测试中...")
        self.api_status_label.configure(text="⏳ 正在测试连接...", text_color="gray")
        self.update_idletasks()
        
        def test_task():
            try:
                url = self.deepseek_url_entry.get().strip() or "https://api.deepseek.com/chat/completions"
                model = self.deepseek_model_var.get()
                
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
                
                data = {
                    "model": model,
                    "messages": [{"role": "user", "content": "Hi"}],
                    "max_tokens": 5
                }
                
                with httpx.Client(timeout=30.0) as client:
                    response = client.post(url, headers=headers, json=data)
                    response.raise_for_status()
                    result = response.json()
                    
                    if "choices" in result:
                        self.api_status_label.configure(text=f"✅ 连接成功！模型 {model} 可用", text_color="green")
                    else:
                        self.api_status_label.configure(text="⚠️ 连接成功但响应异常", text_color="orange")
                        
            except httpx.HTTPStatusError as e:
                error_msg = f"❌ API错误: {e.response.status_code}"
                try:
                    error_detail = e.response.json().get('error', {}).get('message', '')
                    if error_detail:
                        error_msg += f" - {error_detail[:50]}"
                except:
                    pass
                self.api_status_label.configure(text=error_msg, text_color="red")
            except httpx.ConnectError:
                self.api_status_label.configure(text="❌ 无法连接到服务器", text_color="red")
            except httpx.TimeoutException:
                self.api_status_label.configure(text="❌ 连接超时", text_color="red")
            except Exception as e:
                self.api_status_label.configure(text=f"❌ 错误: {str(e)[:50]}", text_color="red")
            finally:
                self.btn_test_api.configure(state="normal", text="🔗 测试连接")
        
        threading.Thread(target=test_task, daemon=True).start()
    
    def fetch_deepseek_models(self):
        """获取DeepSeek可用模型列表"""
        api_key = self.deepseek_key_entry.get().strip()
        if not api_key:
            self.api_status_label.configure(text="❌ 请先输入API密钥", text_color="red")
            return
        
        self.btn_refresh_models.configure(state="disabled", text="获取中...")
        self.api_status_label.configure(text="⏳ 正在获取模型列表...", text_color="gray")
        self.update_idletasks()
        
        def fetch_task():
            try:
                url = "https://api.deepseek.com/models"
                
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
                
                with httpx.Client(timeout=30.0) as client:
                    response = client.get(url, headers=headers)
                    response.raise_for_status()
                    result = response.json()
                    
                    if "data" in result:
                        models = [m.get("id", "") for m in result["data"] if m.get("id")]
                        if models:
                            self.deepseek_models = models
                            self.deepseek_model_combo.configure(values=models)
                            self.api_status_label.configure(text=f"✅ 获取到 {len(models)} 个可用模型", text_color="green")
                        else:
                            self.api_status_label.configure(text="⚠️ 未获取到模型列表", text_color="orange")
                    else:
                        self.api_status_label.configure(text="⚠️ 响应格式异常", text_color="orange")
                        
            except httpx.HTTPStatusError as e:
                self.api_status_label.configure(text=f"❌ 获取失败: {e.response.status_code}", text_color="red")
            except Exception as e:
                self.api_status_label.configure(text=f"❌ 错误: {str(e)[:50]}", text_color="red")
            finally:
                self.btn_refresh_models.configure(state="normal", text="🔄 获取模型")
        
        threading.Thread(target=fetch_task, daemon=True).start()
    
    def setup_drag_drop(self):
        """设置拖拽支持"""
        if HAS_WINDND:
            # 使用windnd实现Windows拖拽
            def on_drop(files):
                if files:
                    # files是bytes列表，需要解码
                    file_path = files[0].decode('gbk') if isinstance(files[0], bytes) else files[0]
                    if file_path.lower().endswith('.srt'):
                        self.srt_path.set(file_path)
                        self.log(f"📂 已拖入文件: {os.path.basename(file_path)}")
                    else:
                        self.log("⚠️ 请拖入SRT格式的字幕文件")
            
            # 绑定到整个窗口
            windnd.hook_dropfiles(self, func=on_drop)
            self.log("✅ 拖拽功能已启用，可直接将SRT文件拖入窗口")
    
    def log(self, message: str, end="\n"):
        """添加日志"""
        self.log_text.insert("end", message + end)
        self.log_text.see("end")
        self.update_idletasks()
    
    def clear_log(self):
        """清空日志"""
        self.log_text.delete("1.0", "end")
    
    def browse_srt(self):
        """选择SRT文件"""
        file_path = filedialog.askopenfilename(
            title="选择SRT字幕文件",
            filetypes=[("SRT文件", "*.srt"), ("所有文件", "*.*")]
        )
        if file_path:
            self.srt_path.set(file_path)
    
    def set_buttons_state(self, enabled: bool):
        """设置按钮状态"""
        state = "normal" if enabled else "disabled"
        self.btn_run.configure(state=state)
    
    def validate_inputs(self) -> bool:
        """验证输入"""
        srt = self.srt_path.get().strip()
        if not srt or not os.path.exists(srt):
            messagebox.showerror("错误", "请选择有效的SRT文件！")
            return False
        
        prompt = self.prompt_text.get("1.0", "end-1c").strip()
        if not prompt:
            messagebox.showerror("错误", "请在「提示词」标签页输入提示词！")
            self.tabview.set("📝 提示词")
            return False
        
        api_key = self.deepseek_key_entry.get().strip()
        if not api_key:
            messagebox.showerror("错误", "请在「API设置」标签页输入API密钥！")
            self.tabview.set("🔑 API设置")
            return False
        
        try:
            split_dur = float(self.split_duration.get())
            overlap_dur = float(self.overlap_duration.get())
            if split_dur <= 0:
                messagebox.showerror("错误", "拆分时长必须大于0！")
                return False
            if overlap_dur < 0:
                messagebox.showerror("错误", "重叠时长不能为负数！")
                return False
        except ValueError:
            messagebox.showerror("错误", "请输入有效的时长数值！")
            return False
        
        return True
    
    def split_srt(self, srt_path: str, split_duration: float, overlap_duration: float) -> List[str]:
        """拆分SRT文件，返回生成的文件列表"""
        subtitles = parse_srt(srt_path)
        if not subtitles:
            self.log("❌ 未能解析到任何字幕内容")
            return []
        
        total_duration = max(s.end_time for s in subtitles)
        self.log(f"📄 字幕文件: {os.path.basename(srt_path)}")
        self.log(f"📊 字幕条数: {len(subtitles)}")
        self.log(f"⏱️  总时长: {format_time_for_display(total_duration)}")
        self.log(f"✂️  拆分时长: {split_duration} 分钟")
        self.log(f"🔄 重叠时长: {overlap_duration} 分钟")
        self.log("-" * 50)
        
        split_sec = split_duration * 60
        overlap_sec = overlap_duration * 60
        root_dir = os.path.dirname(srt_path)
        base_name = Path(srt_path).stem
        
        # 创建拆分字幕文件夹
        split_dir = os.path.join(root_dir, "拆分字幕")
        os.makedirs(split_dir, exist_ok=True)
        self.log(f"📁 拆分字幕保存到: {split_dir}")
        
        generated_files = []
        part_num = 1
        current_start = 0
        
        while current_start < total_duration:
            current_end = current_start + split_sec + overlap_sec
            part_subtitles = get_subtitles_in_range(subtitles, current_start, current_end)
            
            if part_subtitles:
                md_content = subtitles_to_markdown(part_subtitles)
                output_file = os.path.join(split_dir, f"{base_name}-Part{part_num:02d}.md")
                
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(md_content)
                
                generated_files.append(output_file)
                self.log(f"✅ Part {part_num:02d}: {format_time_for_display(current_start)} - {format_time_for_display(current_end)} ({len(part_subtitles)} 条字幕)")
            
            current_start += split_sec
            part_num += 1
        
        self.log("-" * 50)
        self.log(f"🎉 拆分完成！共生成 {len(generated_files)} 个文件")
        return generated_files
    
    def call_deepseek_stream(self, prompt: str, content: str, api_key: str):
        """调用DeepSeek API（流式输出）"""
        url = self.deepseek_url_entry.get().strip() or "https://api.deepseek.com/chat/completions"
        model = self.deepseek_model_var.get() or "deepseek-chat"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": model,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": content}
            ],
            "stream": True
        }
        
        with httpx.Client(timeout=300.0) as client:
            with client.stream("POST", url, headers=headers, json=data) as response:
                response.raise_for_status()
                
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            text = delta.get("content", "")
                            if text:
                                yield text
                        except json.JSONDecodeError:
                            continue
    
    def process_with_ai(self, md_files: List[str], prompt: str, root_dir: str, base_name: str, api_key: str):
        """使用AI处理多个MD文件"""
        self.log(f"📝 提示词已加载 ({len(prompt)} 字符)")
        self.log(f"📂 待处理文件: {len(md_files)} 个")
        self.log("=" * 50)
        
        # 创建分段总结文件夹
        summary_dir = os.path.join(root_dir, "分段总结")
        os.makedirs(summary_dir, exist_ok=True)
        self.log(f"📁 分段总结保存到: {summary_dir}")
        
        all_results = []
        
        for i, md_file in enumerate(md_files, 1):
            file_name = os.path.basename(md_file)
            self.log(f"\n🔄 [{i}/{len(md_files)}] 正在处理: {file_name}")
            self.log("-" * 40)
            
            with open(md_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            result_parts = []
            try:
                for text_chunk in self.call_deepseek_stream(prompt, content, api_key):
                    self.log(text_chunk, end="")
                    result_parts.append(text_chunk)
                self.log("")
            except httpx.HTTPStatusError as e:
                self.log(f"\n❌ API请求失败: {e.response.status_code}")
                self.log(f"   错误信息: {e.response.text}")
                continue
            except Exception as e:
                self.log(f"\n❌ 处理出错: {str(e)}")
                continue
            
            result = ''.join(result_parts)
            all_results.append(result)
            
            # 保存分段总结
            part_summary_file = os.path.join(summary_dir, f"{base_name}-Part{i:02d}.md")
            with open(part_summary_file, 'w', encoding='utf-8') as f:
                f.write(result)
            
            self.log(f"\n✅ {file_name} 处理完成，已保存分段总结")
        
        self.log("\n" + "=" * 50)
        self.log("📑 正在合并所有结果...")
        
        merged_content = "\n\n---\n\n".join(all_results)
        
        # 保存合并后的MD文件到根目录
        output_md = os.path.join(root_dir, f"{base_name}-总结.md")
        with open(output_md, 'w', encoding='utf-8') as f:
            f.write(merged_content)
        self.log(f"✅ MD文件已保存: {output_md}")
    
    def run_full_pipeline(self):
        """完整流程"""
        if self.is_processing:
            return
        if not self.validate_inputs():
            return
        
        self.is_processing = True
        self.set_buttons_state(False)
        
        # 切换到主界面查看日志
        self.tabview.set("🏠 主界面")
        
        def task():
            try:
                self.log("\n" + "=" * 50)
                self.log("       🚀 开始处理")
                self.log("=" * 50 + "\n")
                
                # 拆分
                srt_path = self.srt_path.get().strip()
                md_files = self.split_srt(
                    srt_path,
                    float(self.split_duration.get()),
                    float(self.overlap_duration.get())
                )
                
                if not md_files:
                    self.log("❌ 拆分失败，无法继续AI处理")
                    return
                
                # AI处理
                self.log("\n" + "=" * 50)
                self.log("       🤖 开始AI处理")
                self.log("=" * 50 + "\n")
                
                root_dir = os.path.dirname(srt_path)
                base_name = Path(srt_path).stem
                
                prompt = self.prompt_text.get("1.0", "end-1c").strip()
                api_key = self.deepseek_key_entry.get().strip()
                
                self.process_with_ai(md_files, prompt, root_dir, base_name, api_key)
                
                self.log("\n🎉 全部处理完成！")
                self.log(f"📁 文件保存位置: {root_dir}")
                self.log(f"   - 拆分字幕/  ({len(md_files)} 个文件)")
                self.log(f"   - 分段总结/  ({len(md_files)} 个文件)")
                self.log(f"   - {base_name}-总结.md")
            except Exception as e:
                self.log(f"\n❌ 发生错误: {str(e)}")
            finally:
                self.is_processing = False
                self.set_buttons_state(True)
        
        threading.Thread(target=task, daemon=True).start()


if __name__ == "__main__":
    app = SRTSplitterApp()
    app.mainloop()
