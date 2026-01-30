import os
import logging
from dotenv import load_dotenv

load_dotenv()

# API اطلاعات
API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH', '')
PHONE_NUMBER = os.getenv('PHONE_NUMBER', '')

# تنظیمات مانیتورینگ
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', 300))
MESSAGES_TO_CHECK = int(os.getenv('MESSAGES_TO_CHECK', 5))
TARGET_EXTENSION = os.getenv('TARGET_EXTENSION', '.npvt').lower()
DESTINATION_CHANNEL = os.getenv('DESTINATION_CHANNEL', '').strip()

# تنظیمات نام‌گذاری
FILE_PREFIX = os.getenv('FILE_PREFIX', 'Hamipn_')
SHOW_SEQUENCE_NUMBER = os.getenv('SHOW_SEQUENCE_NUMBER', 'true').lower() == 'true'

# لیست کانال‌های مبدا
source_channels_str = os.getenv('SOURCE_CHANNELS', '')
SOURCE_CHANNELS = [c.strip() for c in source_channels_str.split(',') if c.strip()]

# مسیر فایل سشن (برای ذخیره دائمی)
SESSION_FILE = 'userbot_session.session'

# لاگ
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

def validate_config():
    """اعتبارسنجی تنظیمات"""
    errors = []
    
    if not API_ID or not API_HASH:
        errors.append("API_ID و API_HASH ضروری هستند")
    
    if not PHONE_NUMBER:
        errors.append("PHONE_NUMBER ضروری است")
    
    if not DESTINATION_CHANNEL:
        errors.append("DESTINATION_CHANNEL ضروری است")
    
    if not SOURCE_CHANNELS:
        errors.append("حداقل یک کانال مبدا ضروری است")
    
    if errors:
        for error in errors:
            logger.error(f"❌ {error}")
        raise ValueError("تنظیمات ناقص")
    
    logger.info("✅ تنظیمات اعتبارسنجی شد")
    logger.info(f"📡 کانال‌های مبدا: {len(SOURCE_CHANNELS)}")
    logger.info(f"🎯 پسوند هدف: {TARGET_EXTENSION}")
    logger.info(f"🏷️  کانال مقصد: {DESTINATION_CHANNEL}")
    logger.info(f"📝 پیشوند فایل: {FILE_PREFIX}")
    logger.info(f"🔢 نمایش شماره ترتیب: {SHOW_SEQUENCE_NUMBER}")