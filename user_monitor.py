import asyncio
import logging
import sys
import signal
from datetime import datetime
from typing import Optional, List

from telethon import TelegramClient, events
from telethon.tl.types import (
    Message, MessageMediaDocument, Document, 
    DocumentAttributeFilename, Channel
)
from telethon.errors import (
    ChannelPrivateError, FloodWaitError,
    ChatAdminRequiredError, UserNotParticipantError
)

import aiosqlite
import config

# استفاده از logger از config
logger = config.logger

class TelegramChannelMonitor:
    """کلاس اصلی برای مانیتورینگ کانال‌های تلگرام"""
    
    def __init__(self):
        """مقداردهی اولیه"""
        self.client = TelegramClient(
            session='user_session',
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            device_model="UserBot Monitor",
            system_version="1.0",
            app_version="1.0",
            lang_code="fa",
            system_lang_code="fa-IR"
        )
        
        self.source_channels = config.SOURCE_CHANNELS
        self.destination_channel = config.DESTINATION_CHANNEL
        self.target_extension = config.TARGET_EXTENSION
        self.messages_to_check = config.MESSAGES_TO_CHECK
        self.check_interval = config.CHECK_INTERVAL
        
        self.db_conn: Optional[aiosqlite.Connection] = None
        self.is_running = True
        
        logger.info("🚀 UserBot مانیتورینگ راه‌اندازی شد")
    
    async def init_database(self):
        """راه‌اندازی دیتابیس SQLite"""
        self.db_conn = await aiosqlite.connect(config.DATABASE_FILE)
        
        # ایجاد جدول برای پیام‌های ارسال شده
        await self.db_conn.execute('''
            CREATE TABLE IF NOT EXISTS sent_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                channel_username TEXT,
                file_name TEXT NOT NULL,
                file_size INTEGER,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(message_id, channel_id)
            )
        ''')
        
        # ایجاد جدول برای لاگ فعالیت‌ها
        await self.db_conn.execute('''
            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # ایجاد ایندکس برای بهبود عملکرد
        await self.db_conn.execute('''
            CREATE INDEX IF NOT EXISTS idx_sent_messages 
            ON sent_messages(message_id, channel_id)
        ''')
        
        await self.db_conn.commit()
        logger.info("✅ دیتابیس راه‌اندازی شد")
    
    async def log_activity(self, action: str, details: str = ""):
        """ثبت فعالیت در دیتابیس"""
        if self.db_conn:
            await self.db_conn.execute(
                'INSERT INTO activity_log (action, details) VALUES (?, ?)',
                (action, details)
            )
            await self.db_conn.commit()
    
    async def is_message_processed(self, message_id: int, channel_id: int) -> bool:
        """بررسی آیا پیام قبلاً پردازش شده است"""
        if not self.db_conn:
            return False
        
        cursor = await self.db_conn.execute(
            'SELECT 1 FROM sent_messages WHERE message_id = ? AND channel_id = ?',
            (message_id, channel_id)
        )
        result = await cursor.fetchone()
        await cursor.close()
        
        return result is not None
    
    async def mark_message_as_sent(self, message: Message, channel: Channel, filename: str):
        """علامت‌گذاری پیام به عنوان ارسال شده"""
        if not self.db_conn:
            return
        
        file_size = message.media.document.size if hasattr(message.media.document, 'size') else 0
        
        await self.db_conn.execute('''
            INSERT OR IGNORE INTO sent_messages 
            (message_id, channel_id, channel_username, file_name, file_size)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            message.id,
            channel.id,
            getattr(channel, 'username', str(channel.id)),
            filename,
            file_size
        ))
        
        await self.db_conn.commit()
        await self.log_activity("FILE_SENT", f"{filename} از {channel.id}")
    
    async def authenticate_user(self):
        """احراز هویت کاربر"""
        await self.client.connect()
        
        if not await self.client.is_user_authorized():
            logger.info("🔐 در حال ارسال کد تأیید...")
            
            try:
                # ارسال کد تأیید
                sent_code = await self.client.send_code_request(config.PHONE_NUMBER)
                logger.info(f"📱 کد تأیید به {config.PHONE_NUMBER} ارسال شد")
                
                # دریافت کد از کاربر
                code = input("✏️  لطفاً کد تأیید 5 رقمی را وارد کنید: ").strip()
                
                # تأیید کد
                await self.client.sign_in(config.PHONE_NUMBER, code)
                
                logger.info("✅ احراز هویت موفقیت‌آمیز بود")
                await self.log_activity("AUTH_SUCCESS")
                
            except Exception as e:
                logger.error(f"❌ خطا در احراز هویت: {e}")
                
                # اگر نیاز به رمز دومرحله‌ای است
                if "two-step verification" in str(e).lower():
                    password = input("🔑 لطفاً رمز دومرحله‌ای را وارد کنید: ")
                    await self.client.sign_in(password=password)
                else:
                    raise
        
        # نمایش اطلاعات کاربر
        me = await self.client.get_me()
        logger.info(f"👤 کاربر وارد شده: {me.first_name} (@{me.username or 'بدون نام کاربری'})")
        
        return me
    
    async def check_channel_access(self):
        """بررسی دسترسی به کانال‌ها"""
        logger.info("🔍 بررسی دسترسی به کانال‌ها...")
        
        accessible_channels = []
        
        for channel_username in self.source_channels:
            try:
                entity = await self.client.get_entity(channel_username)
                
                if hasattr(entity, 'title'):
                    logger.info(f"✅ دسترسی به: {entity.title} (@{getattr(entity, 'username', 'private')})")
                    accessible_channels.append(entity)
                else:
                    logger.warning(f"⚠️  موجودیت ناشناس: {channel_username}")
                    
            except ChannelPrivateError:
                logger.error(f"❌ کانال {channel_username} خصوصی است. لطفاً عضو شوید.")
            except ValueError as e:
                logger.error(f"❌ کانال {channel_username} پیدا نشد: {e}")
            except Exception as e:
                logger.error(f"❌ خطا در دسترسی به {channel_username}: {e}")
        
        # بررسی کانال مقصد
        try:
            dest_entity = await self.client.get_entity(self.destination_channel)
            logger.info(f"✅ دسترسی به کانال مقصد: {getattr(dest_entity, 'title', self.destination_channel)}")
        except Exception as e:
            logger.error(f"❌ خطا در دسترسی به کانال مقصد: {e}")
            raise
        
        return accessible_channels
    
    def extract_filename(self, document: Document) -> Optional[str]:
        """استخراج نام فایل از داکیومنت"""
        for attr in document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                return attr.file_name
        return None
    
    async def process_message(self, message: Message, channel: Channel) -> bool:
        """پردازش یک پیام و بررسی فایل"""
        try:
            # بررسی وجود مدیا و نوع آن
            if not message.media or not isinstance(message.media, MessageMediaDocument):
                return False
            
            # بررسی قبلاً پردازش شده
            if await self.is_message_processed(message.id, channel.id):
                return False
            
            # استخراج نام فایل
            document = message.media.document
            filename = self.extract_filename(document)
            
            if not filename:
                return False
            
            filename_lower = filename.lower()
            
            # بررسی پسوند مورد نظر
            if not filename_lower.endswith(self.target_extension):
                return False
            
            logger.info(f"🎯 فایل {self.target_extension} یافت شد: {filename}")
            
            # ارسال به کانال مقصد
            await self.forward_message(message, channel, filename)
            
            return True
            
        except Exception as e:
            logger.error(f"❌ خطا در پردازش پیام {message.id}: {e}")
            return False
    
    async def forward_message(self, message: Message, source_channel: Channel, filename: str):
        """ارسال پیام به کانال مقصد"""
        try:
            # فوروارد پیام
            await self.client.forward_messages(
                entity=self.destination_channel,
                messages=message.id,
                from_peer=source_channel.id
            )
            
            logger.info(f"📤 فایل {filename} ارسال شد")
            
            # ذخیره در دیتابیس
            await self.mark_message_as_sent(message, source_channel, filename)
            
            await self.log_activity("FORWARD_SUCCESS", filename)
            
        except FloodWaitError as e:
            logger.warning(f"⏳ محدودیت FloodWait. انتظار {e.seconds} ثانیه...")
            await asyncio.sleep(e.seconds)
            await self.forward_message(message, source_channel, filename)
            
        except ChatAdminRequiredError:
            logger.error("❌ نیاز به دسترسی ادمین در کانال مقصد")
            
        except Exception as e:
            logger.error(f"❌ خطا در ارسال فایل: {e}")
    
    async def check_channel_messages(self, channel_entity) -> int:
        """بررسی پیام‌های یک کانال"""
        sent_count = 0
        
        try:
            logger.info(f"🔎 در حال بررسی کانال: {getattr(channel_entity, 'title', 'Unknown')}")
            
            # دریافت آخرین پیام‌ها
            messages = await self.client.get_messages(
                entity=channel_entity,
                limit=self.messages_to_check
            )
            
            if not messages:
                logger.info(f"📭 هیچ پیامی در کانال یافت نشد")
                return 0
            
            # پردازش پیام‌ها از جدید به قدیم
            for message in messages:
                if await self.process_message(message, channel_entity):
                    sent_count += 1
                    await asyncio.sleep(1)  # وقفه بین ارسال‌ها
            
            return sent_count
            
        except ChannelPrivateError:
            logger.warning(f"🔒 کانال خصوصی است. نیاز به عضویت: {getattr(channel_entity, 'title', 'Unknown')}")
            return 0
            
        except UserNotParticipantError:
            logger.warning(f"👥 شما عضو این کانال نیستید: {getattr(channel_entity, 'title', 'Unknown')}")
            return 0
            
        except Exception as e:
            logger.error(f"❌ خطا در بررسی کانال: {e}")
            return 0
    
    async def monitor_cycle(self):
        """یک سیکل کامل مانیتورینگ"""
        try:
            logger.info("=" * 50)
            logger.info("🔄 شروع سیکل مانیتورینگ")
            
            # بررسی دسترسی به کانال‌ها
            channels = await self.check_channel_access()
            
            if not channels:
                logger.warning("⚠️  هیچ کانال قابل دسترسی یافت نشد")
                return
            
            total_sent = 0
            
            # بررسی هر کانال
            for channel in channels:
                sent = await self.check_channel_messages(channel)
                total_sent += sent
                
                # وقفه بین بررسی کانال‌ها
                if channel != channels[-1]:
                    await asyncio.sleep(2)
            
            logger.info(f"✅ سیکل کامل شد. {total_sent} فایل ارسال شد")
            logger.info("=" * 50)
            
            await self.log_activity("CYCLE_COMPLETE", f"ارسال شده: {total_sent}")
            
        except Exception as e:
            logger.error(f"❌ خطا در سیکل مانیتورینگ: {e}")
            await self.log_activity("CYCLE_ERROR", str(e))
    
    async def show_statistics(self):
        """نمایش آمار"""
        if not self.db_conn:
            return
        
        cursor = await self.db_conn.execute('''
            SELECT 
                COUNT(*) as total_files,
                COUNT(DISTINCT channel_id) as total_channels,
                SUM(file_size) as total_size
            FROM sent_messages
        ''')
        
        stats = await cursor.fetchone()
        await cursor.close()
        
        if stats:
            total_files, total_channels, total_size = stats
            
            # تبدیل بایت به مگابایت
            total_size_mb = total_size / (1024 * 1024) if total_size else 0
            
            logger.info("📊 آمار ربات:")
            logger.info(f"   📁 کل فایل‌های ارسال شده: {total_files}")
            logger.info(f"   📡 تعداد کانال‌ها: {total_channels}")
            logger.info(f"   💾 حجم کل: {total_size_mb:.2f} MB")
    
    async def start_monitoring(self):
        """شروع مانیتورینگ"""
        try:
            # اعتبارسنجی تنظیمات
            config.validate_config()
            
            # راه‌اندازی دیتابیس
            await self.init_database()
            
            # احراز هویت
            await self.authenticate_user()
            
            logger.info(f"⏱️  فاصله بررسی: هر {self.check_interval} ثانیه")
            logger.info(f"📨 بررسی {self.messages_to_check} پیام آخر هر کانال")
            logger.info(f"🎯 جستجوی فایل‌های: *{self.target_extension}")
            logger.info("🟢 ربات فعال است. برای توقف Ctrl+C را بفشارید.")
            
            # نمایش آمار اولیه
            await self.show_statistics()
            
            # حلقه اصلی مانیتورینگ
            cycle_count = 0
            
            while self.is_running:
                cycle_count += 1
                logger.info(f"\n📊 سیکل شماره: {cycle_count}")
                
                await self.monitor_cycle()
                
                # نمایش آمار هر 10 سیکل
                if cycle_count % 10 == 0:
                    await self.show_statistics()
                
                # انتظار برای سیکل بعدی
                if self.is_running:
                    logger.info(f"⏳ انتظار {self.check_interval} ثانیه برای سیکل بعدی...")
                    
                    # انتظار با قابلیت توقف
                    for _ in range(self.check_interval):
                        if not self.is_running:
                            break
                        await asyncio.sleep(1)
            
        except KeyboardInterrupt:
            logger.info("\n🛑 دریافت سیگنال توقف...")
        except Exception as e:
            logger.error(f"❌ خطای غیرمنتظره: {e}")
            raise
        finally:
            await self.cleanup()
    
    async def cleanup(self):
        """پاکسازی منابع"""
        self.is_running = False
        
        if self.db_conn:
            await self.db_conn.close()
            logger.info("✅ دیتابیس بسته شد")
        
        if self.client.is_connected():
            await self.client.disconnect()
            logger.info("✅ اتصال تلگرام بسته شد")
        
        logger.info("👋 ربات با موفقیت متوقف شد")
    
    def setup_signal_handlers(self):
        """تنظیم هندلرهای سیگنال"""
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
    
    def signal_handler(self, signum, frame):
        """هندلر سیگنال‌های توقف"""
        logger.info(f"📡 دریافت سیگنال توقف ({signum})")
        self.is_running = False

async def main():
    """تابع اصلی اجرا"""
    monitor = TelegramChannelMonitor()
    
    # تنظیم هندلرهای سیگنال
    monitor.setup_signal_handlers()
    
    # اجرای مانیتورینگ
    await monitor.start_monitoring()

if __name__ == "__main__":
    # تنظیم encoding برای ویندوز
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
    
    # اجرای اصلی
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 خداحافظ!")
    except Exception as e:
        logger.error(f"خطای بحرانی: {e}")
        sys.exit(1)