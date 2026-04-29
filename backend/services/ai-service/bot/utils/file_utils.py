import pathlib
import requests
from utils.log_utils import log
from urllib import request

def download(file_url, save_path):
    log.info(file_url)
    log.info("文件开始下载... 来源:{}".format(file_url))
    # 获取文件扩展名
    extension = get_file_extension(file_url)
    file_name = save_path + extension
    try:
        request.urlretrieve(file_url, file_name)
        log.info("文件下载完成,地址:{}".format(save_path))
        return "success", save_path
    except:
        log.info("文件下载失败!")
        return "failed", ""

def get_file_extension(file_path):
    # 普通后缀处理
    extension = pathlib.Path(file_path).suffix
    # 加密链接处理
    if "?" in extension:
        extension = extension.split("?")[0]
    return extension

def download_file(url: str, filename: str):
    """
    通过url下载文件到指定本地路径
    :param url:
    :param filename:
    :return:
    """
    log.info(f"Downloading {url} -> {filename}")
    response = requests.get(url, stream=True, verify=False, timeout=300)
    if response.status_code == 200:
        with open(filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        log.info(f"Downloaded {filename}")
    else:
        log.warning(f"Failed to download {filename}: HTTP {response.status_code}")