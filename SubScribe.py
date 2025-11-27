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
        
        # 初始化日志文件
        self.setup_log_file()
        
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
        self.should_stop = False
    
    def setup_log_file(self):
        """初始化日志文件"""
        try:
            # 创建log文件夹
            log_dir = os.path.join(get_app_path(), 'log')
            os.makedirs(log_dir, exist_ok=True)
            
            # 创建日志文件，命名为当前时间
            timestamp = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
            self.log_file_path = os.path.join(log_dir, f'{timestamp}.log')
            
            # 打开日志文件
            self.log_file = open(self.log_file_path, 'w', encoding='utf-8')
            self.log_file.write(f"=== 日志开始 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n\n")
            self.log_file.flush()
        except Exception as e:
            self.log_file = None
            print(f"日志文件初始化失败: {e}")
    
    def close_log_file(self):
        """关闭日志文件"""
        if hasattr(self, 'log_file') and self.log_file:
            try:
                self.log_file.write(f"\n=== 日志结束 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                self.log_file.close()
            except:
                pass
    
    def load_config(self):
        """加载配置文件"""
        config_path = os.path.join(get_app_path(), 'config.json')
        
        # 默认的3个API配置
        self.default_api_configs = [
            {
                'name': 'DeepSeek',
                'key': '',
                'url': 'https://api.deepseek.com/chat/completions',
                'models': ['deepseek-chat', 'deepseek-coder', 'deepseek-reasoner']
            },
            {
                'name': '自定义API 2',
                'key': '',
                'url': '',
                'models': []
            },
            {
                'name': '自定义API 3',
                'key': '',
                'url': '',
                'models': []
            }
        ]
        
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.api_keys = config.get('api_keys', {})
                    
                    # 加载API配置列表
                    self.api_configs = config.get('api_configs', self.default_api_configs)
                    # 确保有3个配置
                    while len(self.api_configs) < 3:
                        self.api_configs.append(self.default_api_configs[len(self.api_configs)])
                    
                    # 兼容旧格式
                    if 'deepseek_api_key' in config and not self.api_configs[0].get('key'):
                        self.api_configs[0]['key'] = config['deepseek_api_key']
                    if 'deepseek' in self.api_keys and not self.api_configs[0].get('key'):
                        self.api_configs[0]['key'] = self.api_keys['deepseek']
                    if self.api_keys.get('deepseek_url') and not self.api_configs[0].get('url'):
                        self.api_configs[0]['url'] = self.api_keys['deepseek_url']
                    
                    # 加载分段总结提示词
                    self.saved_prompt = config.get('prompt', '')
                    # 加载公众号文章提示词
                    self.saved_article_prompt = config.get('article_prompt', '')
                    
                    # 加载任务的API和模型选择
                    self.saved_summary_api = config.get('summary_api', 0)
                    self.saved_summary_model = config.get('summary_model', 'deepseek-chat')
                    self.saved_article_api = config.get('article_api', 0)
                    self.saved_article_model = config.get('article_model', 'deepseek-reasoner')
            except:
                self.api_configs = self.default_api_configs.copy()
                self.saved_prompt = ''
                self.saved_article_prompt = ''
                self.saved_summary_api = 0
                self.saved_summary_model = 'deepseek-chat'
                self.saved_article_api = 0
                self.saved_article_model = 'deepseek-reasoner'
        else:
            self.api_configs = self.default_api_configs.copy()
            self.saved_prompt = ''
            self.saved_article_prompt = ''
            self.saved_summary_api = 0
            self.saved_summary_model = 'deepseek-chat'
            self.saved_article_api = 0
            self.saved_article_model = 'deepseek-reasoner'
    
    def save_config(self):
        """保存配置"""
        config_path = os.path.join(get_app_path(), 'config.json')
        
        # 获取当前所有API配置
        for i in range(3):
            self.api_configs[i]['name'] = self.api_name_entries[i].get().strip()
            self.api_configs[i]['key'] = self.api_key_entries[i].get().strip()
            self.api_configs[i]['url'] = self.api_url_entries[i].get().strip()
            # 保存模型列表（如果有的话）
            current_models = self.api_configs[i].get('models', [])
            if current_models:
                self.api_configs[i]['models'] = current_models
        
        config = {
            'api_configs': self.api_configs,
            'prompt': self.prompt_text.get("1.0", "end-1c"),
            'article_prompt': self.article_prompt_text.get("1.0", "end-1c"),
            'summary_api': self.summary_api_var.get(),
            'summary_model': self.summary_model_var.get(),
            'article_api': self.article_api_var.get(),
            'article_model': self.article_model_var.get(),
            # 保持旧格式兼容
            'api_keys': self.api_keys,
            'deepseek_api_key': self.api_configs[0].get('key', '')
        }
        
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
        
        self.log("✅ 配置已保存")
    
    def clear_all_prompts(self):
        """清空所有提示词"""
        self.prompt_text.delete("1.0", "end")
        self.article_prompt_text.delete("1.0", "end")
    
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
        btn_frame.grid_columnconfigure((0, 1, 2), weight=1)
        
        self.btn_run = ctk.CTkButton(btn_frame, text="🚀 开始处理", font=("Microsoft YaHei", 14, "bold"),
                                      height=50, fg_color="#28a745", hover_color="#218838", command=self.run_full_pipeline)
        self.btn_run.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        
        self.btn_stop = ctk.CTkButton(btn_frame, text="⏹️ 停止处理", font=("Microsoft YaHei", 14, "bold"),
                                       height=50, fg_color="#dc3545", hover_color="#c82333", command=self.stop_processing,
                                       state="disabled")
        self.btn_stop.grid(row=0, column=1, padx=10, pady=10, sticky="ew")
        
        self.btn_clear = ctk.CTkButton(btn_frame, text="🗑️ 清空日志", font=("Microsoft YaHei", 14, "bold"),
                                        height=50, fg_color="#6c757d", hover_color="#5a6268", command=self.clear_log)
        self.btn_clear.grid(row=0, column=2, padx=10, pady=10, sticky="ew")
        
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
        self.tab_prompt.grid_rowconfigure(3, weight=1)
        
        # ===== 分段总结提示词区域 =====
        hint_label = ctk.CTkLabel(self.tab_prompt, 
                                   text="📝 分段总结提示词（用于处理每一段字幕内容）：",
                                   font=("Microsoft YaHei", 13, "bold"))
        hint_label.grid(row=0, column=0, padx=15, pady=(15, 5), sticky="w")
        
        # 分段总结提示词输入框
        self.prompt_text = ctk.CTkTextbox(self.tab_prompt, font=("Microsoft YaHei", 13), wrap="word")
        self.prompt_text.grid(row=1, column=0, padx=15, pady=(0, 10), sticky="nsew")
        
        # 加载已保存的分段总结提示词
        if hasattr(self, 'saved_prompt') and self.saved_prompt:
            self.prompt_text.insert("1.0", self.saved_prompt)
        
        # ===== 公众号文章提示词区域 =====
        article_hint_label = ctk.CTkLabel(self.tab_prompt, 
                                           text="📰 公众号文章提示词（用于将合并总结生成公众号文章）：",
                                           font=("Microsoft YaHei", 13, "bold"))
        article_hint_label.grid(row=2, column=0, padx=15, pady=(10, 5), sticky="w")
        
        # 公众号文章提示词输入框
        self.article_prompt_text = ctk.CTkTextbox(self.tab_prompt, font=("Microsoft YaHei", 13), wrap="word")
        self.article_prompt_text.grid(row=3, column=0, padx=15, pady=(0, 10), sticky="nsew")
        
        # 加载已保存的公众号文章提示词
        if hasattr(self, 'saved_article_prompt') and self.saved_article_prompt:
            self.article_prompt_text.insert("1.0", self.saved_article_prompt)
        
        # 按钮
        btn_frame = ctk.CTkFrame(self.tab_prompt, fg_color="transparent")
        btn_frame.grid(row=4, column=0, padx=15, pady=(0, 15), sticky="ew")
        
        ctk.CTkButton(btn_frame, text="💾 保存所有提示词", font=("Microsoft YaHei", 13),
                      command=self.save_config).pack(side="right", padx=5)
        
        ctk.CTkButton(btn_frame, text="🗑️ 清空全部", font=("Microsoft YaHei", 13),
                      fg_color="#dc3545", hover_color="#c82333",
                      command=self.clear_all_prompts).pack(side="right", padx=5)
    
    def show_model_selector(self, combobox, title="选择模型"):
        """显示模型选择对话框，支持搜索和滚轮滚动"""
        values = list(combobox.cget("values"))
        if not values or values[0] in ['(请获取模型列表)', '(请先获取模型)']:
            messagebox.showinfo("提示", "请先点击「获取」按钮获取模型列表")
            return
        
        # 创建对话框
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("450x500")
        dialog.transient(self)
        dialog.grab_set()
        
        # 居中显示
        dialog.update_idletasks()
        x = self.winfo_x() + (self.winfo_width() - 450) // 2
        y = self.winfo_y() + (self.winfo_height() - 500) // 2
        dialog.geometry(f"+{x}+{y}")
        
        dialog.grid_columnconfigure(0, weight=1)
        dialog.grid_rowconfigure(1, weight=1)
        
        # 搜索框
        search_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        search_frame.grid(row=0, column=0, padx=15, pady=(15, 10), sticky="ew")
        search_frame.grid_columnconfigure(1, weight=1)
        
        ctk.CTkLabel(search_frame, text="🔍", font=("Microsoft YaHei", 14)).grid(row=0, column=0, padx=(0, 5))
        search_var = ctk.StringVar()
        search_entry = ctk.CTkEntry(search_frame, textvariable=search_var, 
                                     placeholder_text="输入关键词搜索模型...",
                                     font=("Microsoft YaHei", 12))
        search_entry.grid(row=0, column=1, sticky="ew")
        
        # 模型数量标签
        count_label = ctk.CTkLabel(dialog, text=f"共 {len(values)} 个模型", 
                                    font=("Microsoft YaHei", 11), text_color="gray")
        count_label.grid(row=0, column=0, padx=15, pady=0, sticky="e")
        
        # 可滚动的模型列表
        list_frame = ctk.CTkScrollableFrame(dialog)
        list_frame.grid(row=1, column=0, padx=15, pady=10, sticky="nsew")
        list_frame.grid_columnconfigure(0, weight=1)
        
        # 存储按钮引用
        model_buttons = []
        selected_model = [combobox.get()]  # 使用列表以便在闭包中修改
        
        def select_model(model):
            selected_model[0] = model
            combobox.set(model)
            dialog.destroy()
        
        def create_model_buttons(filter_text=""):
            # 清除现有按钮
            for btn in model_buttons:
                btn.destroy()
            model_buttons.clear()
            
            # 过滤模型
            filter_lower = filter_text.lower()
            filtered = [m for m in values if filter_lower in m.lower()] if filter_text else values
            
            # 更新计数
            count_label.configure(text=f"显示 {len(filtered)}/{len(values)} 个模型")
            
            # 创建按钮
            for i, model in enumerate(filtered):
                is_selected = model == selected_model[0]
                btn = ctk.CTkButton(
                    list_frame, 
                    text=model,
                    font=("Consolas", 11),
                    height=32,
                    anchor="w",
                    fg_color="#1f6aa5" if is_selected else "transparent",
                    hover_color="#144870" if is_selected else "#3d3d3d",
                    text_color="white" if is_selected else None,
                    command=lambda m=model: select_model(m)
                )
                btn.grid(row=i, column=0, padx=2, pady=1, sticky="ew")
                model_buttons.append(btn)
        
        # 搜索框变化时更新列表
        def on_search_change(*args):
            create_model_buttons(search_var.get())
        
        search_var.trace_add("write", on_search_change)
        
        # 初始化列表
        create_model_buttons()
        
        # 按钮区域
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.grid(row=2, column=0, padx=15, pady=(5, 15), sticky="ew")
        
        ctk.CTkButton(btn_frame, text="取消", width=80,
                      fg_color="#6c757d", hover_color="#5a6268",
                      command=dialog.destroy).pack(side="right", padx=5)
        
        # 聚焦搜索框
        search_entry.focus_set()
    
    def setup_api_tab(self):
        """设置API标签页"""
        self.tab_api.grid_columnconfigure(0, weight=1)
        self.tab_api.grid_rowconfigure(0, weight=1)
        
        # 创建滚动容器
        scroll_frame = ctk.CTkScrollableFrame(self.tab_api)
        scroll_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        scroll_frame.grid_columnconfigure(0, weight=1)
        
        # 存储API相关的控件
        self.api_name_entries = []
        self.api_key_entries = []
        self.api_url_entries = []
        self.api_model_combos = []
        self.api_status_labels = []
        self.api_test_btns = []
        self.api_fetch_btns = []
        
        # 创建3个API配置区域
        for i in range(3):
            api_frame = ctk.CTkFrame(scroll_frame)
            api_frame.grid(row=i, column=0, padx=5, pady=8, sticky="ew")
            api_frame.grid_columnconfigure(1, weight=1)
            
            # API名称
            ctk.CTkLabel(api_frame, text=f"🔌 API {i+1}", font=("Microsoft YaHei", 14, "bold")).grid(
                row=0, column=0, padx=15, pady=(10, 5), sticky="w")
            
            name_entry = ctk.CTkEntry(api_frame, font=("Microsoft YaHei", 12), width=150,
                                       placeholder_text="API名称")
            name_entry.grid(row=0, column=1, padx=(5, 10), pady=(10, 5), sticky="w")
            name_entry.insert(0, self.api_configs[i].get('name', f'API {i+1}'))
            self.api_name_entries.append(name_entry)
            
            # API密钥
            ctk.CTkLabel(api_frame, text="密钥:", font=("Microsoft YaHei", 12)).grid(
                row=1, column=0, padx=15, pady=5, sticky="w")
            key_entry = ctk.CTkEntry(api_frame, font=("Microsoft YaHei", 11), show="•")
            key_entry.grid(row=1, column=1, padx=(5, 10), pady=5, sticky="ew")
            if self.api_configs[i].get('key'):
                key_entry.insert(0, self.api_configs[i]['key'])
            self.api_key_entries.append(key_entry)
            
            # 测试按钮
            test_btn = ctk.CTkButton(api_frame, text="🔗 测试", width=70,
                                      font=("Microsoft YaHei", 11),
                                      command=lambda idx=i: self.test_api(idx))
            test_btn.grid(row=1, column=2, padx=(5, 15), pady=5)
            self.api_test_btns.append(test_btn)
            
            # 接口地址
            ctk.CTkLabel(api_frame, text="地址:", font=("Microsoft YaHei", 12)).grid(
                row=2, column=0, padx=15, pady=5, sticky="w")
            url_entry = ctk.CTkEntry(api_frame, font=("Microsoft YaHei", 11),
                                      placeholder_text="https://api.example.com/chat/completions")
            url_entry.grid(row=2, column=1, columnspan=2, padx=(5, 15), pady=5, sticky="ew")
            if self.api_configs[i].get('url'):
                url_entry.insert(0, self.api_configs[i]['url'])
            self.api_url_entries.append(url_entry)
            
            # 模型列表和获取按钮
            ctk.CTkLabel(api_frame, text="模型:", font=("Microsoft YaHei", 12)).grid(
                row=3, column=0, padx=15, pady=5, sticky="w")
            
            models = self.api_configs[i].get('models', [])
            if not models:
                models = ['(请获取模型列表)']
            
            model_frame = ctk.CTkFrame(api_frame, fg_color="transparent")
            model_frame.grid(row=3, column=1, padx=(5, 10), pady=5, sticky="ew")
            
            model_combo = ctk.CTkComboBox(model_frame, values=models,
                                           font=("Microsoft YaHei", 11), width=160)
            model_combo.pack(side="left")
            if models and models[0] != '(请获取模型列表)':
                model_combo.set(models[0])
            self.api_model_combos.append(model_combo)
            
            # 浏览按钮
            browse_btn = ctk.CTkButton(model_frame, text="📋", width=30,
                                        font=("Microsoft YaHei", 11),
                                        command=lambda combo=model_combo: self.show_model_selector(combo, "选择模型"))
            browse_btn.pack(side="left", padx=(5, 0))
            
            fetch_btn = ctk.CTkButton(api_frame, text="🔄 获取", width=70,
                                       font=("Microsoft YaHei", 11),
                                       command=lambda idx=i: self.fetch_models(idx))
            fetch_btn.grid(row=3, column=2, padx=(5, 15), pady=5)
            self.api_fetch_btns.append(fetch_btn)
            
            # 状态显示
            status_label = ctk.CTkLabel(api_frame, text="", font=("Microsoft YaHei", 11))
            status_label.grid(row=4, column=0, columnspan=3, padx=15, pady=(0, 10), sticky="w")
            self.api_status_labels.append(status_label)
        
        # ===== 任务API选择区域 =====
        task_frame = ctk.CTkFrame(scroll_frame)
        task_frame.grid(row=3, column=0, padx=5, pady=15, sticky="ew")
        task_frame.grid_columnconfigure((1, 3), weight=1)
        
        ctk.CTkLabel(task_frame, text="📋 任务API配置", font=("Microsoft YaHei", 14, "bold")).grid(
            row=0, column=0, columnspan=4, padx=15, pady=(10, 15), sticky="w")
        
        # 分段总结 - API选择
        ctk.CTkLabel(task_frame, text="分段总结:", font=("Microsoft YaHei", 12, "bold")).grid(
            row=1, column=0, padx=(15, 5), pady=8, sticky="w")
        
        api_names = [self.api_configs[i].get('name', f'API {i+1}') for i in range(3)]
        self.summary_api_var = ctk.IntVar(value=getattr(self, 'saved_summary_api', 0))
        self.summary_api_combo = ctk.CTkComboBox(task_frame, values=api_names, width=150,
                                                  font=("Microsoft YaHei", 11),
                                                  command=self.on_summary_api_change)
        self.summary_api_combo.grid(row=1, column=1, padx=5, pady=8, sticky="w")
        self.summary_api_combo.set(api_names[self.summary_api_var.get()])
        
        ctk.CTkLabel(task_frame, text="模型:", font=("Microsoft YaHei", 12)).grid(
            row=1, column=2, padx=(15, 5), pady=8, sticky="w")
        
        summary_models = self.api_configs[self.summary_api_var.get()].get('models', ['deepseek-chat'])
        if not summary_models:
            summary_models = ['(请先获取模型)']
        
        summary_model_frame = ctk.CTkFrame(task_frame, fg_color="transparent")
        summary_model_frame.grid(row=1, column=3, padx=(5, 15), pady=8, sticky="w")
        
        self.summary_model_var = ctk.StringVar(value=getattr(self, 'saved_summary_model', 'deepseek-chat'))
        self.summary_model_combo = ctk.CTkComboBox(summary_model_frame, values=summary_models, width=150,
                                                    font=("Microsoft YaHei", 11),
                                                    variable=self.summary_model_var)
        self.summary_model_combo.pack(side="left")
        
        ctk.CTkButton(summary_model_frame, text="📋", width=30,
                      font=("Microsoft YaHei", 11),
                      command=lambda: self.show_model_selector(self.summary_model_combo, "选择分段总结模型")).pack(side="left", padx=(5, 0))
        
        # 公众号文章 - API选择
        ctk.CTkLabel(task_frame, text="公众号文章:", font=("Microsoft YaHei", 12, "bold")).grid(
            row=2, column=0, padx=(15, 5), pady=8, sticky="w")
        
        self.article_api_var = ctk.IntVar(value=getattr(self, 'saved_article_api', 0))
        self.article_api_combo = ctk.CTkComboBox(task_frame, values=api_names, width=150,
                                                  font=("Microsoft YaHei", 11),
                                                  command=self.on_article_api_change)
        self.article_api_combo.grid(row=2, column=1, padx=5, pady=8, sticky="w")
        self.article_api_combo.set(api_names[self.article_api_var.get()])
        
        ctk.CTkLabel(task_frame, text="模型:", font=("Microsoft YaHei", 12)).grid(
            row=2, column=2, padx=(15, 5), pady=8, sticky="w")
        
        article_models = self.api_configs[self.article_api_var.get()].get('models', ['deepseek-reasoner'])
        if not article_models:
            article_models = ['(请先获取模型)']
        
        article_model_frame = ctk.CTkFrame(task_frame, fg_color="transparent")
        article_model_frame.grid(row=2, column=3, padx=(5, 15), pady=8, sticky="w")
        
        self.article_model_var = ctk.StringVar(value=getattr(self, 'saved_article_model', 'deepseek-reasoner'))
        self.article_model_combo = ctk.CTkComboBox(article_model_frame, values=article_models, width=150,
                                                    font=("Microsoft YaHei", 11),
                                                    variable=self.article_model_var)
        self.article_model_combo.pack(side="left")
        
        ctk.CTkButton(article_model_frame, text="📋", width=30,
                      font=("Microsoft YaHei", 11),
                      command=lambda: self.show_model_selector(self.article_model_combo, "选择公众号文章模型")).pack(side="left", padx=(5, 0))
        
        # 保存按钮
        ctk.CTkButton(scroll_frame, text="💾 保存所有设置", font=("Microsoft YaHei", 13, "bold"),
                      height=40, command=self.save_config).grid(row=4, column=0, padx=5, pady=15, sticky="e")
    
    def on_summary_api_change(self, choice):
        """分段总结API选择变化时更新模型列表"""
        api_names = [self.api_configs[i].get('name', f'API {i+1}') for i in range(3)]
        try:
            idx = api_names.index(choice)
            self.summary_api_var.set(idx)
            models = self.api_configs[idx].get('models', [])
            if not models:
                models = ['(请先获取模型)']
            self.summary_model_combo.configure(values=models)
            if models:
                self.summary_model_combo.set(models[0])
        except ValueError:
            pass
    
    def on_article_api_change(self, choice):
        """公众号文章API选择变化时更新模型列表"""
        api_names = [self.api_configs[i].get('name', f'API {i+1}') for i in range(3)]
        try:
            idx = api_names.index(choice)
            self.article_api_var.set(idx)
            models = self.api_configs[idx].get('models', [])
            if not models:
                models = ['(请先获取模型)']
            self.article_model_combo.configure(values=models)
            if models:
                self.article_model_combo.set(models[0])
        except ValueError:
            pass
    
    def test_api(self, api_idx: int):
        """测试指定API连接"""
        api_key = self.api_key_entries[api_idx].get().strip()
        api_url = self.api_url_entries[api_idx].get().strip()
        
        if not api_key:
            self.api_status_labels[api_idx].configure(text="❌ 请先输入API密钥", text_color="red")
            return
        if not api_url:
            self.api_status_labels[api_idx].configure(text="❌ 请先输入接口地址", text_color="red")
            return
        
        self.api_test_btns[api_idx].configure(state="disabled", text="测试中...")
        self.api_status_labels[api_idx].configure(text="⏳ 正在测试连接...", text_color="gray")
        self.update_idletasks()
        
        def test_task():
            try:
                # 获取模型，优先使用已配置的模型
                models = self.api_configs[api_idx].get('models', [])
                model_combo_value = self.api_model_combos[api_idx].get()
                
                if model_combo_value and model_combo_value != '(请获取模型列表)':
                    model = model_combo_value
                elif models:
                    model = models[0]
                else:
                    # 没有模型时提示用户先获取模型列表
                    self.api_status_labels[api_idx].configure(
                        text="⚠️ 请先点击「获取」按钮获取模型列表", text_color="orange")
                    self.api_test_btns[api_idx].configure(state="normal", text="🔗 测试")
                    return
                
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
                    response = client.post(api_url, headers=headers, json=data)
                    response.raise_for_status()
                    result = response.json()
                    
                    if "choices" in result:
                        self.api_status_labels[api_idx].configure(
                            text=f"✅ 连接成功！模型: {model}", text_color="green")
                    else:
                        self.api_status_labels[api_idx].configure(
                            text="⚠️ 连接成功但响应异常", text_color="orange")
                        
            except httpx.HTTPStatusError as e:
                error_msg = f"❌ API错误: {e.response.status_code}"
                try:
                    error_detail = e.response.json().get('message', '') or e.response.json().get('error', {}).get('message', '')
                    if error_detail:
                        error_msg += f" - {error_detail[:30]}"
                except:
                    pass
                self.api_status_labels[api_idx].configure(text=error_msg, text_color="red")
            except httpx.ConnectError:
                self.api_status_labels[api_idx].configure(text="❌ 无法连接到服务器", text_color="red")
            except httpx.TimeoutException:
                self.api_status_labels[api_idx].configure(text="❌ 连接超时", text_color="red")
            except Exception as e:
                self.api_status_labels[api_idx].configure(text=f"❌ 错误: {str(e)[:40]}", text_color="red")
            finally:
                self.api_test_btns[api_idx].configure(state="normal", text="🔗 测试")
        
        threading.Thread(target=test_task, daemon=True).start()
    
    def fetch_models(self, api_idx: int):
        """获取指定API的模型列表"""
        api_key = self.api_key_entries[api_idx].get().strip()
        api_url = self.api_url_entries[api_idx].get().strip()
        
        if not api_key:
            self.api_status_labels[api_idx].configure(text="❌ 请先输入API密钥", text_color="red")
            return
        
        self.api_fetch_btns[api_idx].configure(state="disabled", text="获取中...")
        self.api_status_labels[api_idx].configure(text="⏳ 正在获取模型列表...", text_color="gray")
        self.update_idletasks()
        
        def fetch_task():
            try:
                # 尝试从URL推断models端点
                if api_url:
                    # 去掉 /chat/completions 部分，保留 /v1
                    if '/chat/completions' in api_url:
                        models_url = api_url.replace('/chat/completions', '/models')
                    elif api_url.endswith('/v1'):
                        models_url = f"{api_url}/models"
                    else:
                        # 尝试找到基础URL并添加/models
                        base_url = api_url.rstrip('/')
                        models_url = f"{base_url}/models"
                else:
                    models_url = "https://api.deepseek.com/models"
                
                headers = {
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                }
                
                with httpx.Client(timeout=30.0) as client:
                    response = client.get(models_url, headers=headers)
                    response.raise_for_status()
                    result = response.json()
                    
                    if "data" in result:
                        models = [m.get("id", "") for m in result["data"] if m.get("id")]
                        if models:
                            self.api_configs[api_idx]['models'] = models
                            self.api_model_combos[api_idx].configure(values=models)
                            self.api_model_combos[api_idx].set(models[0])
                            
                            # 更新任务选择的模型列表
                            if self.summary_api_var.get() == api_idx:
                                self.summary_model_combo.configure(values=models)
                            if self.article_api_var.get() == api_idx:
                                self.article_model_combo.configure(values=models)
                            
                            self.api_status_labels[api_idx].configure(
                                text=f"✅ 获取到 {len(models)} 个模型", text_color="green")
                        else:
                            self.api_status_labels[api_idx].configure(
                                text="⚠️ 未获取到模型列表", text_color="orange")
                    else:
                        self.api_status_labels[api_idx].configure(
                            text="⚠️ 响应格式异常", text_color="orange")
                        
            except httpx.HTTPStatusError as e:
                self.api_status_labels[api_idx].configure(
                    text=f"❌ 获取失败: {e.response.status_code}", text_color="red")
            except Exception as e:
                self.api_status_labels[api_idx].configure(
                    text=f"❌ 错误: {str(e)[:40]}", text_color="red")
            finally:
                self.api_fetch_btns[api_idx].configure(state="normal", text="🔄 获取")
        
        threading.Thread(target=fetch_task, daemon=True).start()
    
    def setup_drag_drop(self):
        """设置拖拽支持"""
        if HAS_WINDND:
            # 使用windnd实现Windows拖拽
            def on_drop(files):
                try:
                    if files:
                        raw_path = files[0]
                        # 尝试多种编码解码
                        if isinstance(raw_path, bytes):
                            for encoding in ['utf-8', 'gbk', 'cp936', 'latin-1']:
                                try:
                                    file_path = raw_path.decode(encoding)
                                    break
                                except UnicodeDecodeError:
                                    continue
                            else:
                                file_path = raw_path.decode('utf-8', errors='replace')
                        else:
                            file_path = str(raw_path)
                        
                        # 清理路径
                        file_path = file_path.strip().strip('"').strip("'")
                        
                        if file_path.lower().endswith('.srt'):
                            self.srt_path.set(file_path)
                            self.log(f"📂 已拖入文件: {os.path.basename(file_path)}")
                        else:
                            self.log(f"⚠️ 请拖入SRT格式的字幕文件（当前: {os.path.basename(file_path)}）")
                except Exception as e:
                    self.log(f"⚠️ 拖拽文件处理出错: {str(e)}")
            
            try:
                # 绑定到整个窗口
                windnd.hook_dropfiles(self, func=on_drop)
                self.log("✅ 拖拽功能已启用，可直接将SRT文件拖入窗口")
            except Exception as e:
                self.log(f"⚠️ 拖拽功能初始化失败: {str(e)}")
        else:
            self.log("💡 提示：安装 windnd 库可启用拖拽功能 (pip install windnd)")
    
    def log(self, message: str, end="\n"):
        """添加日志到界面和文件"""
        # 写入界面
        self.log_text.insert("end", message + end)
        self.log_text.see("end")
        self.update_idletasks()
        
        # 写入日志文件
        if hasattr(self, 'log_file') and self.log_file:
            try:
                # 添加时间戳
                timestamp = datetime.now().strftime('%H:%M:%S')
                # 清理消息中的特殊字符用于日志
                clean_message = message.replace('\r', '')
                self.log_file.write(f"[{timestamp}] {clean_message}{end}")
                self.log_file.flush()  # 实时刷新
            except:
                pass
    
    def clear_log(self):
        """清空日志"""
        self.log_text.delete("1.0", "end")
        # 在日志文件中添加分隔符
        if hasattr(self, 'log_file') and self.log_file:
            try:
                self.log_file.write(f"\n--- 日志已清空 {datetime.now().strftime('%H:%M:%S')} ---\n\n")
                self.log_file.flush()
            except:
                pass
    
    def browse_srt(self):
        """选择SRT文件"""
        file_path = filedialog.askopenfilename(
            title="选择SRT字幕文件",
            filetypes=[("SRT文件", "*.srt"), ("所有文件", "*.*")]
        )
        if file_path:
            self.srt_path.set(file_path)
    
    def stop_processing(self):
        """停止处理"""
        if self.is_processing:
            self.should_stop = True
            self.log("\n⚠️ 正在停止处理...")
            self.btn_stop.configure(state="disabled", text="正在停止...")
    
    def set_buttons_state(self, enabled: bool):
        """设置按钮状态"""
        state = "normal" if enabled else "disabled"
        stop_state = "disabled" if enabled else "normal"
        self.btn_run.configure(state=state)
        self.btn_stop.configure(state=stop_state, text="⏹️ 停止处理")
    
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
        
        # 验证分段总结的API配置
        summary_api_idx = self.summary_api_var.get()
        summary_api_key = self.api_key_entries[summary_api_idx].get().strip()
        summary_api_url = self.api_url_entries[summary_api_idx].get().strip()
        
        if not summary_api_key:
            api_name = self.api_name_entries[summary_api_idx].get() or f"API {summary_api_idx + 1}"
            messagebox.showerror("错误", f"请在「API设置」标签页为 {api_name} 输入API密钥！")
            self.tabview.set("🔑 API设置")
            return False
        
        if not summary_api_url:
            api_name = self.api_name_entries[summary_api_idx].get() or f"API {summary_api_idx + 1}"
            messagebox.showerror("错误", f"请在「API设置」标签页为 {api_name} 输入接口地址！")
            self.tabview.set("🔑 API设置")
            return False
        
        # 如果有公众号文章提示词，验证公众号文章的API配置
        article_prompt = self.article_prompt_text.get("1.0", "end-1c").strip()
        if article_prompt:
            article_api_idx = self.article_api_var.get()
            article_api_key = self.api_key_entries[article_api_idx].get().strip()
            article_api_url = self.api_url_entries[article_api_idx].get().strip()
            
            if not article_api_key:
                api_name = self.api_name_entries[article_api_idx].get() or f"API {article_api_idx + 1}"
                messagebox.showerror("错误", f"请在「API设置」标签页为 {api_name} 输入API密钥！")
                self.tabview.set("🔑 API设置")
                return False
            
            if not article_api_url:
                api_name = self.api_name_entries[article_api_idx].get() or f"API {article_api_idx + 1}"
                messagebox.showerror("错误", f"请在「API设置」标签页为 {api_name} 输入接口地址！")
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
    
    def call_api_stream(self, prompt: str, content: str, api_idx: int, model: str):
        """调用指定API（流式输出）"""
        url = self.api_url_entries[api_idx].get().strip()
        api_key = self.api_key_entries[api_idx].get().strip()
        
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
        
        with httpx.Client(timeout=600.0) as client:
            with client.stream("POST", url, headers=headers, json=data) as response:
                response.raise_for_status()
                
                for line in response.iter_lines():
                    # 检查是否需要停止
                    if self.should_stop:
                        raise InterruptedError("用户已停止处理")
                    
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
    
    def process_with_ai(self, md_files: List[str], prompt: str, root_dir: str, base_name: str):
        """使用AI处理多个MD文件"""
        # 获取分段总结的API和模型配置
        api_idx = self.summary_api_var.get()
        model = self.summary_model_var.get()
        api_name = self.api_name_entries[api_idx].get() or f"API {api_idx + 1}"
        
        self.log(f"📝 提示词已加载 ({len(prompt)} 字符)")
        self.log(f"🔌 使用API: {api_name}")
        self.log(f"🤖 使用模型: {model}")
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
                for text_chunk in self.call_api_stream(prompt, content, api_idx, model):
                    # 检查是否需要停止
                    if self.should_stop:
                        self.log("\n⛔ 用户已停止处理")
                        return None
                    self.log(text_chunk, end="")
                    result_parts.append(text_chunk)
                self.log("")
            except InterruptedError:
                self.log("\n⛔ 用户已停止处理")
                return None
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
            
            # 检查是否需要停止
            if self.should_stop:
                self.log("\n⛔ 用户已停止处理")
                return None
        
        self.log("\n" + "=" * 50)
        self.log("📑 正在合并所有结果...")
        
        merged_content = "\n\n---\n\n".join(all_results)
        
        # 保存合并后的MD文件到根目录
        output_md = os.path.join(root_dir, f"{base_name}-总结.md")
        with open(output_md, 'w', encoding='utf-8') as f:
            f.write(merged_content)
        self.log(f"✅ MD文件已保存: {output_md}")
        
        return merged_content
    
    def generate_article(self, merged_content: str, article_prompt: str, root_dir: str, base_name: str):
        """使用AI生成公众号文章"""
        self.log("\n" + "=" * 50)
        self.log("       📰 开始生成公众号文章")
        self.log("=" * 50 + "\n")
        
        # 获取公众号文章的API和模型配置
        api_idx = self.article_api_var.get()
        model = self.article_model_var.get()
        api_name = self.api_name_entries[api_idx].get() or f"API {api_idx + 1}"
        
        self.log(f"📝 公众号文章提示词已加载 ({len(article_prompt)} 字符)")
        self.log(f"🔌 使用API: {api_name}")
        self.log(f"🤖 使用模型: {model}")
        self.log("-" * 40)
        
        result_parts = []
        try:
            for text_chunk in self.call_api_stream(article_prompt, merged_content, api_idx, model):
                # 检查是否需要停止
                if self.should_stop:
                    self.log("\n⛔ 用户已停止处理")
                    return
                self.log(text_chunk, end="")
                result_parts.append(text_chunk)
            self.log("")
        except InterruptedError:
            self.log("\n⛔ 用户已停止处理")
            return
        except httpx.HTTPStatusError as e:
            self.log(f"\n❌ API请求失败: {e.response.status_code}")
            self.log(f"   错误信息: {e.response.text}")
            return
        except Exception as e:
            self.log(f"\n❌ 处理出错: {str(e)}")
            return
        
        result = ''.join(result_parts)
        
        # 保存公众号文章到根目录
        article_file = os.path.join(root_dir, f"{base_name}-公众号文章.md")
        with open(article_file, 'w', encoding='utf-8') as f:
            f.write(result)
        
        self.log(f"\n✅ 公众号文章已保存: {article_file}")
    
    def run_full_pipeline(self):
        """完整流程"""
        if self.is_processing:
            return
        if not self.validate_inputs():
            return
        
        self.is_processing = True
        self.should_stop = False  # 重置停止标志
        self.set_buttons_state(False)
        
        # 切换到主界面查看日志
        self.tabview.set("🏠 主界面")
        
        def task():
            try:
                self.log("\n" + "=" * 50)
                self.log("       🚀 开始处理")
                self.log("=" * 50 + "\n")
                
                # 检查是否需要停止
                if self.should_stop:
                    self.log("⛔ 用户已停止处理")
                    return
                
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
                
                # 检查是否需要停止
                if self.should_stop:
                    self.log("⛔ 用户已停止处理")
                    return
                
                # AI处理
                self.log("\n" + "=" * 50)
                self.log("       🤖 开始AI处理")
                self.log("=" * 50 + "\n")
                
                root_dir = os.path.dirname(srt_path)
                base_name = Path(srt_path).stem
                
                prompt = self.prompt_text.get("1.0", "end-1c").strip()
                
                merged_content = self.process_with_ai(md_files, prompt, root_dir, base_name)
                
                # 检查是否需要停止或处理失败
                if self.should_stop or merged_content is None:
                    if self.should_stop:
                        self.log("⛔ 用户已停止处理")
                    return
                
                # 获取公众号文章提示词
                article_prompt = self.article_prompt_text.get("1.0", "end-1c").strip()
                
                # 如果有公众号文章提示词，则生成公众号文章
                if article_prompt:
                    self.generate_article(merged_content, article_prompt, root_dir, base_name)
                    
                    if self.should_stop:
                        return
                    
                    self.log("\n🎉 全部处理完成！")
                    self.log(f"📁 文件保存位置: {root_dir}")
                    self.log(f"   - 拆分字幕/  ({len(md_files)} 个文件)")
                    self.log(f"   - 分段总结/  ({len(md_files)} 个文件)")
                    self.log(f"   - {base_name}-总结.md")
                    self.log(f"   - {base_name}-公众号文章.md")
                else:
                    self.log("\n💡 提示：未设置公众号文章提示词，跳过公众号文章生成")
                    self.log("\n🎉 处理完成！")
                    self.log(f"📁 文件保存位置: {root_dir}")
                    self.log(f"   - 拆分字幕/  ({len(md_files)} 个文件)")
                    self.log(f"   - 分段总结/  ({len(md_files)} 个文件)")
                    self.log(f"   - {base_name}-总结.md")
            except InterruptedError:
                self.log("\n⛔ 用户已停止处理")
            except Exception as e:
                self.log(f"\n❌ 发生错误: {str(e)}")
            finally:
                self.is_processing = False
                self.should_stop = False  # 重置停止标志
                self.set_buttons_state(True)
        
        threading.Thread(target=task, daemon=True).start()


if __name__ == "__main__":
    app = SRTSplitterApp()
    
    # 程序关闭时关闭日志文件
    def on_closing():
        app.close_log_file()
        app.destroy()
    
    app.protocol("WM_DELETE_WINDOW", on_closing)
    app.mainloop()
