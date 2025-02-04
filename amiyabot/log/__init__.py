import jieba

from .manager import LoggerManager, UserLogger
from .progress import download_progress

jieba.setLogLevel(jieba.logging.INFO)

logger = LoggerManager('Main')

info = logger.info
error = logger.error
debug = logger.debug
catch = logger.catch
warning = logger.warning
critical = logger.critical
sync_catch = logger.sync_catch
