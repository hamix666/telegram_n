import os
import re
from typing import Optional, Tuple
import config

class FileNamingSystem:
    """سیستم نام‌گذاری هوشمند فایل‌ها"""
    
    def __init__(self, prefix: str = None, show_sequence: bool = None):
        self.prefix = prefix or config.FILE_PREFIX
        self.show_sequence = show_sequence if show_sequence is not None else config.SHOW_SEQUENCE_NUMBER
        self.destination_id = config.DESTINATION_CHANNEL.replace('@', '')
    
    def clean_filename(self, filename: str) -> str:
        """پاکسازی نام فایل از کاراکترهای نامعتبر"""
        # حذف پسوند
        if '.' in filename:
            name, ext = filename.rsplit('.', 1)
        else:
            name, ext = filename, ''
        
        # حذف کاراکترهای نامعتبر
        name = re.sub(r'[<>:"/\\|?*]', '', name)
        name = re.sub(r'\s+', ' ', name).strip()
        
        # محدودیت طول
        if len(name) > 50:
            name = name[:50]
        
        return f"{name}.{ext}" if ext else name
    
    def generate_new_filename(self, original_filename: str, sequence_number: int) -> str:
        """تولید نام جدید برای فایل"""
        # پاکسازی نام اصلی
        cleaned_name = self.clean_filename(original_filename)
        
        # استخراج پسوند
        if '.' in cleaned_name:
            name_part, extension = cleaned_name.rsplit('.', 1)
        else:
            name_part, extension = cleaned_name, ''
        
        # ساخت نام جدید
        if self.show_sequence:
            new_name = f"{self.prefix}{sequence_number:04d}_{self.destination_id}"
        else:
            new_name = f"{self.prefix}{self.destination_id}"
        
        # اضافه کردن بخشی از نام اصلی (اختیاری)
        if len(name_part) > 0:
            # فقط 20 کاراکتر اول نام اصلی
            short_name = name_part[:20]
            new_name = f"{new_name}_{short_name}"
        
        # اضافه کردن پسوند
        if extension:
            new_name = f"{new_name}.{extension}"
        
        return new_name
    
    def extract_original_name(self, new_filename: str) -> Optional[str]:
        """استخراج نام اصلی از نام جدید"""
        try:
            # حذف پیشوند و شماره
            pattern = re.compile(rf'^{re.escape(self.prefix)}\d+_{re.escape(self.destination_id)}_')
            match = pattern.match(new_filename)
            
            if match:
                remaining = new_filename[match.end():]
                return remaining
            return None
        except:
            return None