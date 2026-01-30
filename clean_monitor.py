import asyncio
import logging
import os
import sys
import signal
from datetime import datetime
from typing import Optional, Dict, List
import tempfile

from telethon import TelegramClient
from telethon.tl.types import (
    Message, MessageMediaDocument, Document,
    DocumentAttributeFilename, Channel
)
from telethon.errors import (
    FloodWaitError, ChatAdminRequiredError,
    ChannelPrivateError
)

import aiosqlite
import config

logger = config.logger

class CleanFileMonitor:
    """مانیتور کانال‌ها با کپی تمیز فایل‌ها"""
    
    def __init__(self):
        self.client = TelegramClient(
            session='clean_session',
            api_id=config.API_ID,
            api_hash=config.API_HASH
        )
        
        self.source_channels = config.SOURCE_CHANNELS
        self.destination_channel = config.DESTINATION_CHANNEL
        self.target_extension = config.TARGET_EXTENSION
        self.messages_to_check = config.MESSAGES_TO_CHECK
        self.check_interval = config.CHECK_INTERVAL
        
        self.db_conn: Optional[aiosqlite.Connection] = None
        self.is_running = True
        
        # آیدی کانال مقصد برای نمایش در کپشن
        self.destination_id = config.DESTINATION_CHANNEL.replace('@', '')
        
        logger.info("🧼 ربات کپی تمیز فایل‌ها راه‌اندازی شد")
    
    async def init_database(self):
        """راه‌اندازی دیتابیس"""
        self.db_conn = await aiosqlite.connect('clean_messages.db')
        
        await self.db_conn.execute('''
            CREATE TABLE IF NOT EXISTS processed_files (
                file_hash TEXT PRIMARY KEY,
                original_message_id INTEGER,
                channel_id INTEGER,
                file_name TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        await self.db_conn.commit()
        logger.info("✅ دیتابیس راه‌اندازی شد")
    
    def get_file_hash(self, document: Document) -> str:
        """ایجاد هش یکتا برای فایل"""
        file_id = str(document.id)
        file_size = str(document.size)
        return f"{file_id}_{file_size}"
    
    async def is_file_processed(self, file_hash: str) -> bool:
        """بررسی آیا فایل قبلاً پردازش شده است"""
        cursor = await self.db_conn.execute(
            'SELECT 1 FROM processed_files WHERE file_hash = ?',
            (file_hash,)
        )
        result = await cursor.fetchone()
        await cursor.close()
        return result is not None
    
    async def mark_file_as_processed(self, file_hash: str, message_id: int, 
                                    channel_id: int, filename: str):
        """علامت‌گذاری فایل به عنوان پردازش شده"""
        await self.db_conn.execute('''
            INSERT OR REPLACE INTO processed_files 
            (file_hash, original_message_id, channel_id, file_name)
            VALUES (?, ?, ?, ?)
        ''', (file_hash, message_id, channel_id, filename))
        
        await self.db_conn.commit()
    
    async def authenticate(self):
        """احراز هویت کاربر"""
        await self.client.connect()
        
        if not await self.client.is_user_authorized():
            logger.info("🔐 ارسال کد تأیید...")
            await self.client.send_code_request(config.PHONE_NUMBER)
            code = input("✏️  کد تأیید را وارد کنید: ").strip()
            await self.client.sign_in(config.PHONE_NUMBER, code)
        
        me = await self.client.get_me()
        logger.info(f"👤 کاربر: {me.first_name}")
        return me
    
    async def check_destination_access(self):
        """بررسی دسترسی به کانال مقصد"""
        try:
            dest_entity = await self.client.get_entity(self.destination_channel)
            logger.info(f"🎯 کانال مقصد: {getattr(dest_entity, 'title', 'Unknown')}")
            
            # بررسی دسترسی ارسال
            try:
                # ارسال پیام تست
                test_message = await self.client.send_message(
                    dest_entity,
                    "✅ ربات آماده ارسال فایل‌ها است...",
                    silent=True
                )
                await test_message.delete()
                logger.info("✅ دسترسی ارسال تأیید شد")
                return True
            except ChatAdminRequiredError:
                logger.error("❌ نیاز به دسترسی ادمین برای ارسال")
                return False
                
        except Exception as e:
            logger.error(f"❌ خطا در دسترسی به کانال مقصد: {e}")
            return False
    
    async def download_file(self, message: Message, filename: str) -> Optional[str]:
        """دانلود فایل به صورت موقت"""
        try:
            # ایجاد فایل موقت
            temp_dir = tempfile.gettempdir()
            temp_file = os.path.join(temp_dir, f"telegram_{datetime.now().timestamp()}_{filename}")
            
            # دانلود فایل
            logger.info(f"⬇️  در حال دانلود: {filename}")
            downloaded = await self.client.download_media(
                message.media,
                file=temp_file
            )
            
            if downloaded and os.path.exists(downloaded):
                file_size = os.path.getsize(downloaded) / (1024 * 1024)  # به مگابایت
                logger.info(f"✅ دانلود شد: {filename} ({file_size:.2f} MB)")
                return downloaded
            
            return None
            
        except Exception as e:
            logger.error(f"❌ خطا در دانلود {filename}: {e}")
            return None
    
    async def send_clean_file(self, file_path: str, filename: str, 
                             source_channel_name: str = "") -> bool:
        """ارسال فایل کپی شده بدون هیچ اثری از مبدا"""
        try:
            # خواندن فایل
            with open(file_path, 'rb') as f:
                file_data = f.read()
            
            # ایجاد کپشن
            caption_lines = [
                f"📁 **{filename}**",
                "",
                f"🆔 **کانال:** @{self.destination_id}",
                f"📅 **تاریخ:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                "",
                "#فایل #کانال"
            ]
            
            caption = "\n".join(caption_lines)
            
            # ارسال فایل
            await self.client.send_file(
                entity=self.destination_channel,
                file=file_data,
                caption=caption,
                file_name=filename,
                force_document=True,
                silent=True,
                allow_cache=False
            )
            
            logger.info(f"📤 فایل ارسال شد: {filename}")
            return True
            
        except FloodWaitError as e:
            logger.warning(f"⏳ FloodWait: {e.seconds} ثانیه")
            await asyncio.sleep(e.seconds)
            return await self.send_clean_file(file_path, filename, source_channel_name)
            
        except Exception as e:
            logger.error(f"❌ خطا در ارسال فایل: {e}")
            return False
    
    async def process_message(self, message: Message, channel: Channel) -> bool:
        """پردازش پیام و کپی فایل"""
        try:
            # بررسی وجود فایل
            if not message.media or not isinstance(message.media, MessageMediaDocument):
                return False
            
            document = message.media.document
            
            # استخراج نام فایل
            filename = None
            for attr in document.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    filename = attr.file_name
                    break
            
            if not filename:
                return False
            
            filename_lower = filename.lower()
            
            # بررسی پسوند مورد نظر
            if not filename_lower.endswith(self.target_extension):
                return False
            
            # بررسی هش فایل
            file_hash = self.get_file_hash(document)
            if await self.is_file_processed(file_hash):
                logger.info(f"⏭️  فایل قبلاً پردازش شده: {filename}")
                return False
            
            logger.info(f"🎯 فایل پیدا شد: {filename}")
            
            # دانلود فایل
            downloaded_path = await self.download_file(message, filename)
            if not downloaded_path:
                return False
            
            # ارسال فایل کپی شده
            success = await self.send_clean_file(downloaded_path, filename, 
                                                getattr(channel, 'username', ''))
            
            # پاکسازی فایل موقت
            try:
                if os.path.exists(downloaded_path):
                    os.path.exists(downloaded_path)
            except:
                pass
            
            if success:
                # ذخیره در دیتابیس
                await self.mark_file_as_processed(
                    file_hash, message.id, channel.id, filename
                )
                logger.info(f"✅ فایل پردازش شد: {filename}")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"❌ خطا در پردازش پیام: {e}")
            return False
    
    async def check_channel(self, channel_username: str) -> int:
        """بررسی یک کانال"""
        sent_count = 0
        
        try:
            channel = await self.client.get_entity(channel_username)
            channel_title = getattr(channel, 'title', channel_username)
            logger.info(f"🔎 بررسی کانال: {channel_title}")
            
            # دریافت آخرین پیام‌ها
            messages = await self.client.get_messages(
                channel,
                limit=self.messages_to_check
            )
            
            if not messages:
                logger.info(f"📭 پیامی یافت نشد")
                return 0
            
            # پردازش پیام‌ها
            for message in messages:
                if await self.process_message(message, channel):
                    sent_count += 1
                    await asyncio.sleep(2)  # وقفه بین ارسال‌ها
            
            return sent_count
            
        except ChannelPrivateError:
            logger.warning(f"🔒 کانال خصوصی است: {channel_username}")
            return 0
        except Exception as e:
            logger.error(f"❌ خطا در بررسی کانال {channel_username}: {e}")
            return 0
    
    async def monitoring_cycle(self):
        """یک سیکل کامل مانیتورینگ"""
        logger.info("=" * 60)
        logger.info("🔄 شروع سیکل مانیتورینگ")
        
        total_sent = 0
        
        for channel in self.source_channels:
            sent = await self.check_channel(channel)
            total_sent += sent
            
            if channel != self.source_channels[-1]:
                await asyncio.sleep(3)  # وقفه بین کانال‌ها
        
        logger.info(f"✅ سیکل کامل شد. {total_sent} فایل کپی شد")
        logger.info("=" * 60)
        
        return total_sent
    
    async def show_stats(self):
        """نمایش آمار"""
        if not self.db_conn:
            return
        
        cursor = await self.db_conn.execute('''
            SELECT 
                COUNT(*) as total,
                COUNT(DISTINCT channel_id) as channels,
                GROUP_CONCAT(DISTINCT file_name) as recent_files
            FROM processed_files
            ORDER BY processed_at DESC
            LIMIT 5
        ''')
        
        stats = await cursor.fetchone()
        await cursor.close()
        
        if stats:
            total, channels, recent_files = stats
            logger.info("📊 آمار:")
            logger.info(f"   📁 کل فایل‌ها: {total}")
            logger.info(f"   📡 کانال‌های پردازش شده: {channels}")
            if recent_files:
                files = recent_files.split(',')[:3]
                logger.info(f"   🆕 آخرین فایل‌ها: {', '.join(files)}")
    
    async def start(self):
        """شروع مانیتورینگ"""
        try:
            # اعتبارسنجی
            config.validate_config()
            
            # راه‌اندازی دیتابیس
            await self.init_database()
            
            # احراز هویت
            await self.authenticate()
            
            # بررسی دسترسی به مقصد
            if not await self.check_destination_access():
                logger.error("❌ دسترسی به کانال مقصد ممکن نیست")
                return
            
            logger.info(f"⏱️  بررسی هر {self.check_interval} ثانیه")
            logger.info(f"📨 بررسی {self.messages_to_check} پیام آخر هر کانال")
            logger.info(f"🎯 جستجوی فایل‌های: *{self.target_extension}")
            logger.info(f"🏷️  آیدی در پیام‌ها: @{self.destination_id}")
            logger.info("🟢 ربات فعال. Ctrl+C برای توقف")
            
            await self.show_stats()
            
            # حلقه اصلی
            cycle_count = 0
            while self.is_running:
                cycle_count += 1
                logger.info(f"\n📊 سیکل شماره: {cycle_count}")
                
                await self.monitoring_cycle()
                
                # نمایش آمار هر 5 سیکل
                if cycle_count % 5 == 0:
                    await self.show_stats()
                
                # انتظار برای سیکل بعدی
                if self.is_running:
                    logger.info(f"⏳ انتظار {self.check_interval} ثانیه...")
                    for _ in range(self.check_interval):
                        if not self.is_running:
                            break
                        await asyncio.sleep(1)
        
        except KeyboardInterrupt:
            logger.info("\n🛑 توقف درخواست شد...")
        except Exception as e:
            logger.error(f"❌ خطای بحرانی: {e}")
            raise
        finally:
            await self.cleanup()
    
    async def cleanup(self):
        """پاکسازی"""
        self.is_running = False
        
        if self.db_conn:
            await self.db_conn.close()
            logger.info("✅ دیتابیس بسته شد")
        
        if self.client.is_connected():
            await self.client.disconnect()
            logger.info("✅ اتصال تلگرام بسته شد")
        
        logger.info("👋 ربات متوقف شد")
    
    def setup_signals(self):
        """تنظیم سیگنال‌ها"""
        signal.signal(signal.SIGINT, lambda s, f: setattr(self, 'is_running', False))
        signal.signal(signal.SIGTERM, lambda s, f: setattr(self, 'is_running', False))

async def main():
    """تابع اصلی"""
    monitor = CleanFileMonitor()
    monitor.setup_signals()
    await monitor.start()

if __name__ == "__main__":
    # تنظیم encoding
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    
    # اجرا
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 خداحافظ!")
    except Exception as e:
        logger.error(f"خطای اجرا: {e}")
        sys.exit(1)