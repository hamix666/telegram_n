import asyncio
import logging
import os
import sys
import signal
import tempfile
from datetime import datetime
from typing import Optional, Dict, Tuple

from telethon import TelegramClient
from telethon.tl.types import (
    Message, MessageMediaDocument, Document,
    DocumentAttributeFilename, Channel
)
from telethon.errors import (
    FloodWaitError, ChatAdminRequiredError,
    ChannelPrivateError, SessionPasswordNeededError
)

import config
from database import DatabaseManager
from file_namer import FileNamingSystem

logger = config.logger

class AdvancedTelegramMonitor:
    """ربات مانیتورینگ پیشرفته با نام‌گذاری هوشمند - نسخه اصلاح شده"""
    
    def __init__(self):
        # استفاده از فایل سشن دائمی
        self.client = TelegramClient(
            session=config.SESSION_FILE,
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            connection_retries=5,
            retry_delay=1,
            timeout=30
        )
        
        # مدیران
        self.db = DatabaseManager()
        self.namer = FileNamingSystem()
        
        # متغیرهای مدیریت
        self.source_channels = config.SOURCE_CHANNELS
        self.destination_channel = config.DESTINATION_CHANNEL
        self.target_extension = config.TARGET_EXTENSION
        self.messages_to_check = config.MESSAGES_TO_CHECK
        self.check_interval = config.CHECK_INTERVAL
        
        self.is_running = True
        
        logger.info("🚀 ربات پیشرفته مانیتورینگ راه‌اندازی شد")
    
    def get_file_hash(self, document: Document) -> str:
        """ایجاد هش یکتا برای فایل"""
        if hasattr(document, 'id') and hasattr(document, 'size'):
            date_str = str(getattr(document, 'date', datetime.now()).timestamp())
            return f"{document.id}_{document.size}_{date_str}"
        return f"hash_{datetime.now().timestamp()}"
    
    async def authenticate(self):
        """احراز هویت با ذخیره سشن"""
        await self.client.connect()
        
        if not await self.client.is_user_authorized():
            logger.info("🔐 در حال احراز هویت...")
            
            try:
                # ارسال کد
                sent = await self.client.send_code_request(config.PHONE_NUMBER)
                logger.info("📱 کد تأیید ارسال شد")
                
                # دریافت کد از کاربر
                code = input("✏️  لطفاً کد 5 رقمی را وارد کنید: ").strip()
                
                # تلاش برای ورود با کد
                try:
                    await self.client.sign_in(config.PHONE_NUMBER, code)
                    logger.info("✅ ورود با کد موفقیت‌آمیز بود")
                except SessionPasswordNeededError:
                    # اگر رمز دو مرحله‌ای نیاز است
                    password = input("🔑 رمز دو مرحله‌ای را وارد کنید: ")
                    await self.client.sign_in(password=password)
                    logger.info("✅ ورود با رمز دو مرحله‌ای موفق بود")
                
            except Exception as e:
                logger.error(f"❌ خطا در احراز هویت: {e}")
                raise
        
        # نمایش اطلاعات کاربر
        me = await self.client.get_me()
        logger.info(f"👤 کاربر: {me.first_name} (@{me.username or 'بدون نام کاربری'})")
        
        # ذخیره سشن
        logger.info(f"💾 سشن در {config.SESSION_FILE} ذخیره شد")
        
        await self.db.log_activity("USER_AUTHENTICATED", f"@{me.username}")
        
        return me
    
    async def check_destination_access(self):
        """بررسی دسترسی به کانال مقصد"""
        try:
            dest_entity = await self.client.get_entity(self.destination_channel)
            logger.info(f"🎯 کانال مقصد: {getattr(dest_entity, 'title', 'Unknown')}")
            
            return True
                
        except Exception as e:
            logger.error(f"❌ خطا در دسترسی به کانال مقصد: {e}")
            return False
    
    async def download_file(self, message: Message) -> Optional[Tuple[str, str, int]]:
        """دانلود فایل و بازگرداندن مسیر و اطلاعات"""
        try:
            if not message.media or not isinstance(message.media, MessageMediaDocument):
                return None
            
            document = message.media.document
            
            # استخراج نام اصلی فایل
            original_filename = None
            for attr in document.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    original_filename = attr.file_name
                    break
            
            if not original_filename:
                return None
            
            # ایجاد فایل موقت با نام مشخص
            temp_dir = tempfile.gettempdir()
            temp_name = f"tg_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{original_filename}"
            temp_path = os.path.join(temp_dir, temp_name)
            
            # دانلود فایل
            logger.info(f"⬇️  دانلود: {original_filename}")
            downloaded = await self.client.download_media(
                message.media,
                file=temp_path
            )
            
            if downloaded and os.path.exists(downloaded):
                file_size = os.path.getsize(downloaded)
                return downloaded, original_filename, file_size
            
            return None
            
        except Exception as e:
            logger.error(f"❌ خطا در دانلود فایل: {e}")
            return None
    
    async def send_file_with_new_name(self, file_path: str, original_name: str, 
                                     file_size: int, channel_username: str, 
                                     message: Message) -> bool:
        """ارسال فایل با نام جدید و شماره ترتیب"""
        try:
            # دریافت شماره ترتیب بعدی
            sequence_number = await self.db.get_next_sequence_number()
            
            # تولید نام جدید
            new_filename = self.namer.generate_new_filename(original_name, sequence_number)
            
            # خواندن فایل
            with open(file_path, 'rb') as f:
                file_data = f.read()
            
            # ایجاد کپشن
            destination_id = config.DESTINATION_CHANNEL.replace('@', '')
            file_size_mb = file_size / (1024 * 1024)
            
            caption_lines = [
                f"📁 **{new_filename}**",
                "",
                f"🔢 **شماره:** {sequence_number}",
                f"🏷️  **کانال:** @{destination_id}",
                f"📦 **حجم:** {file_size_mb:.2f} MB",
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
                file_name=new_filename,
                force_document=True,
                silent=True,
                allow_cache=False,
                attributes=[DocumentAttributeFilename(new_filename)]
            )
            
            logger.info(f"📤 ارسال شد: {new_filename} (شماره: {sequence_number})")
            
            # ذخیره در دیتابیس - با استفاده از message
            if hasattr(message, 'media') and hasattr(message.media, 'document'):
                file_hash = self.get_file_hash(message.media.document)
            else:
                # ایجاد هش جایگزین
                file_hash = f"{datetime.now().timestamp()}_{original_name}"
            
            await self.db.save_processed_file(
                file_hash=file_hash,
                original_filename=original_name,
                new_filename=new_filename,
                sequence_number=sequence_number,
                channel_username=channel_username,
                file_size=file_size
            )
            
            await self.db.log_activity("FILE_SENT", f"{new_filename} - #{sequence_number}")
            
            return True
            
        except FloodWaitError as e:
            logger.warning(f"⏳ FloodWait: {e.seconds} ثانیه")
            await asyncio.sleep(e.seconds)
            return await self.send_file_with_new_name(file_path, original_name, file_size, channel_username, message)
            
        except Exception as e:
            logger.error(f"❌ خطا در ارسال فایل: {e}")
            await self.db.log_activity("SEND_ERROR", str(e))
            return False
    
    async def process_message(self, message: Message, channel: Channel) -> bool:
        """پردازش پیام"""
        try:
            # بررسی وجود فایل
            if not message.media or not isinstance(message.media, MessageMediaDocument):
                return False
            
            document = message.media.document
            
            # بررسی پسوند
            original_filename = None
            for attr in document.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    original_filename = attr.file_name
                    break
            
            if not original_filename:
                return False
            
            if not original_filename.lower().endswith(self.target_extension):
                return False
            
            logger.info(f"🎯 فایل پیدا شد: {original_filename}")
            
            # بررسی هش فایل (بعد از تأیید پسوند)
            file_hash = self.get_file_hash(document)
            if await self.db.is_file_processed(file_hash):
                logger.info(f"⏭️  فایل قبلاً پردازش شده: {original_filename}")
                return False
            
            # دانلود فایل
            download_result = await self.download_file(message)
            if not download_result:
                return False
            
            file_path, original_name, file_size = download_result
            
            # ارسال با نام جدید
            channel_username = getattr(channel, 'username', str(channel.id))
            success = await self.send_file_with_new_name(
                file_path, original_name, file_size, channel_username, message
            )
            
            # پاکسازی فایل موقت
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as clean_error:
                logger.warning(f"⚠️  خطا در پاکسازی فایل موقت: {clean_error}")
            
            return success
            
        except Exception as e:
            logger.error(f"❌ خطا در پردازش پیام: {e}")
            await self.db.log_activity("PROCESS_ERROR", str(e))
            return False
    
    async def check_channel(self, channel_username: str) -> int:
        """بررسی یک کانال"""
        sent_count = 0
        
        try:
            channel = await self.client.get_entity(channel_username)
            channel_title = getattr(channel, 'title', channel_username)
            logger.info(f"🔎 بررسی کانال: {channel_title}")
            
            # دریافت پیام‌ها
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
                    await asyncio.sleep(3)  # وقفه بین ارسال‌ها
            
            return sent_count
            
        except ChannelPrivateError:
            logger.warning(f"🔒 کانال خصوصی است: {channel_username}")
            return 0
        except Exception as e:
            logger.error(f"❌ خطا در بررسی کانال {channel_username}: {e}")
            return 0
    
    async def monitoring_cycle(self):
        """سیکل مانیتورینگ"""
        logger.info("=" * 60)
        logger.info("🔄 شروع سیکل مانیتورینگ")
        
        total_sent = 0
        
        for channel in self.source_channels:
            sent = await self.check_channel(channel)
            total_sent += sent
            
            if channel != self.source_channels[-1]:
                await asyncio.sleep(2)
        
        logger.info(f"✅ سیکل کامل شد. {total_sent} فایل ارسال شد")
        logger.info("=" * 60)
        
        await self.db.log_activity("CYCLE_COMPLETE", f"ارسال شده: {total_sent}")
        
        return total_sent
    
    async def show_statistics(self):
        """نمایش آمار"""
        try:
            stats = await self.db.get_file_statistics()
            
            logger.info("📊 آمار ربات:")
            logger.info(f"   📁 کل فایل‌های ارسال شده: {stats.get('total_files', 0)}")
            logger.info(f"   🔢 شماره ترتیب فعلی: {stats.get('current_sequence', 0)}")
            
            if stats.get('files_by_channel'):
                logger.info("   📡 بر اساس کانال:")
                for channel, count in stats['files_by_channel']:
                    logger.info(f"      • {channel or 'Unknown'}: {count}")
            
            if stats.get('recent_files'):
                logger.info("   🆕 آخرین فایل‌ها:")
                for filename, seq, date in stats['recent_files'][:3]:
                    short_date = date.split()[0] if date else "Unknown"
                    logger.info(f"      • #{seq}: {filename} ({short_date})")
        except Exception as e:
            logger.error(f"❌ خطا در نمایش آمار: {e}")
    
    async def start(self):
        """شروع مانیتورینگ"""
        try:
            # اعتبارسنجی
            config.validate_config()
            
            # راه‌اندازی دیتابیس
            await self.db.initialize()
            
            # احراز هویت (فقط بار اول نیاز به کد دارد)
            await self.authenticate()
            
            # بررسی دسترسی
            if not await self.check_destination_access():
                logger.error("❌ دسترسی به کانال مقصد ممکن نیست")
                return
            
            logger.info(f"⏱️  بررسی هر {self.check_interval} ثانیه")
            logger.info(f"📨 بررسی {self.messages_to_check} پیام آخر هر کانال")
            logger.info(f"🎯 جستجوی فایل‌های: *{self.target_extension}")
            logger.info(f"📝 الگوی نام: {config.FILE_PREFIX}[شماره]_کانال")
            logger.info("🟢 ربات فعال. Ctrl+C برای توقف")
            
            await self.show_statistics()
            
            # حلقه اصلی
            cycle_count = 0
            while self.is_running:
                cycle_count += 1
                logger.info(f"\n📊 سیکل شماره: {cycle_count}")
                
                sent_count = await self.monitoring_cycle()
                
                # نمایش آمار هر 3 سیکل
                if cycle_count % 3 == 0 or sent_count > 0:
                    await self.show_statistics()
                
                # انتظار برای سیکل بعدی
                if self.is_running:
                    logger.info(f"⏳ انتظار {self.check_interval} ثانیه...")
                    for i in range(self.check_interval):
                        if not self.is_running:
                            break
                        if i % 60 == 0 and i > 0:  # گزارش هر دقیقه
                            logger.info(f"   ⏰ {i//60} دقیقه از {self.check_interval//60} گذشت...")
                        await asyncio.sleep(1)
        
        except KeyboardInterrupt:
            logger.info("\n🛑 توقف درخواست شد...")
        except Exception as e:
            logger.error(f"❌ خطای بحرانی: {e}")
            raise
        finally:
            await self.cleanup()
    
    async def cleanup(self):
        """پاکسازی منابع"""
        self.is_running = False
        
        try:
            await self.db.close()
            logger.info("✅ دیتابیس بسته شد")
        except:
            pass
        
        try:
            if self.client.is_connected():
                await self.client.disconnect()
                logger.info("✅ اتصال تلگرام بسته شد")
        except:
            pass
        
        logger.info("👋 ربات متوقف شد")
    
    def setup_signals(self):
        """تنظیم سیگنال‌ها"""
        def signal_handler(signum, frame):
            logger.info(f"📡 دریافت سیگنال {signum}")
            self.is_running = False
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

def main():
    """تابع اصلی اجرا"""
    monitor = AdvancedTelegramMonitor()
    monitor.setup_signals()
    
    # اجرای async
    asyncio.run(monitor.start())

if __name__ == "__main__":
    # تنظیم encoding برای ویندوز
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
    
    # اجرا
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 خداحافظ!")
    except Exception as e:
        logger.error(f"خطای اجرا: {e}")
        sys.exit(1)