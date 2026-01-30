import aiosqlite
import asyncio
from datetime import datetime
from typing import Optional, Tuple, List, Dict
import config

class DatabaseManager:
    """مدیریت دیتابیس با قابلیت شماره ترتیب"""
    
    def __init__(self, db_file: str = 'telegram_monitor.db'):
        self.db_file = db_file
        self.conn: Optional[aiosqlite.Connection] = None
    
    async def initialize(self):
        """راه‌اندازی دیتابیس"""
        self.conn = await aiosqlite.connect(self.db_file)
        
        # جدول فایل‌های پردازش شده
        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS processed_files (
                file_hash TEXT PRIMARY KEY,
                original_filename TEXT,
                new_filename TEXT,
                sequence_number INTEGER,
                channel_username TEXT,
                file_size INTEGER,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # جدول شمارنده‌ها
        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS counters (
                counter_name TEXT PRIMARY KEY,
                counter_value INTEGER DEFAULT 0,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # جدول لاگ فعالیت‌ها
        await self.conn.execute('''
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT,
                details TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # ایندکس‌ها برای بهبود عملکرد
        await self.conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_seq_number 
            ON processed_files(sequence_number DESC)
        ''')
        
        await self.conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_channel 
            ON processed_files(channel_username)
        ''')
        
        await self.conn.commit()
        
        # مقداردهی اولیه شمارنده اگر وجود ندارد
        await self.init_counter('file_counter')
        
        config.logger.info("✅ دیتابیس راه‌اندازی شد")
    
    async def init_counter(self, counter_name: str):
        """مقداردهی اولیه شمارنده"""
        cursor = await self.conn.execute(
            'SELECT 1 FROM counters WHERE counter_name = ?',
            (counter_name,)
        )
        if not await cursor.fetchone():
            await self.conn.execute(
                'INSERT INTO counters (counter_name, counter_value) VALUES (?, ?)',
                (counter_name, 0)
            )
            await self.conn.commit()
        await cursor.close()
    
    async def get_next_sequence_number(self, counter_name: str = 'file_counter') -> int:
        """دریافت شماره ترتیب بعدی"""
        async with self.conn.execute(
            'SELECT counter_value FROM counters WHERE counter_name = ?',
            (counter_name,)
        ) as cursor:
            result = await cursor.fetchone()
            current_value = result[0] if result else 0
        
        # افزایش شمارنده
        new_value = current_value + 1
        await self.conn.execute(
            'UPDATE counters SET counter_value = ?, last_updated = CURRENT_TIMESTAMP WHERE counter_name = ?',
            (new_value, counter_name)
        )
        await self.conn.commit()
        
        return new_value
    
    async def get_current_sequence_number(self, counter_name: str = 'file_counter') -> int:
        """دریافت شماره ترتیب فعلی"""
        async with self.conn.execute(
            'SELECT counter_value FROM counters WHERE counter_name = ?',
            (counter_name,)
        ) as cursor:
            result = await cursor.fetchone()
            return result[0] if result else 0
    
    async def is_file_processed(self, file_hash: str) -> bool:
        """بررسی پردازش فایل"""
        async with self.conn.execute(
            'SELECT 1 FROM processed_files WHERE file_hash = ?',
            (file_hash,)
        ) as cursor:
            result = await cursor.fetchone()
            return result is not None
    
    async def save_processed_file(self, file_hash: str, original_filename: str, 
                                 new_filename: str, sequence_number: int, 
                                 channel_username: str, file_size: int):
        """ذخیره اطلاعات فایل پردازش شده"""
        await self.conn.execute('''
            INSERT INTO processed_files 
            (file_hash, original_filename, new_filename, sequence_number, 
             channel_username, file_size)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (file_hash, original_filename, new_filename, sequence_number, 
              channel_username, file_size))
        await self.conn.commit()
    
    async def log_activity(self, action: str, details: str = ""):
        """ثبت فعالیت در لاگ"""
        await self.conn.execute(
            'INSERT INTO activity_log (action, details) VALUES (?, ?)',
            (action, details)
        )
        await self.conn.commit()
    
    async def get_file_statistics(self) -> Dict:
        """دریافت آمار فایل‌ها"""
        stats = {}
        
        # تعداد کل فایل‌ها
        async with self.conn.execute(
            'SELECT COUNT(*) FROM processed_files'
        ) as cursor:
            stats['total_files'] = (await cursor.fetchone())[0]
        
        # تعداد فایل‌ها بر اساس کانال
        async with self.conn.execute('''
            SELECT channel_username, COUNT(*) 
            FROM processed_files 
            GROUP BY channel_username
        ''') as cursor:
            stats['files_by_channel'] = await cursor.fetchall()
        
        # آخرین فایل‌ها
        async with self.conn.execute('''
            SELECT new_filename, sequence_number, processed_at 
            FROM processed_files 
            ORDER BY sequence_number DESC 
            LIMIT 5
        ''') as cursor:
            stats['recent_files'] = await cursor.fetchall()
        
        # شماره ترتیب فعلی
        stats['current_sequence'] = await self.get_current_sequence_number()
        
        return stats
    
    async def close(self):
        """بستن اتصال دیتابیس"""
        if self.conn:
            await self.conn.close()