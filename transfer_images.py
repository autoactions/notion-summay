import requests
from notion_client import Client
import json
import os
import logging
import sys

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 从环境变量获取Notion API密钥和图片处理服务URL
NOTION_API_KEY = os.environ.get('NOTION_TOKEN')
IMAGE_HOST = os.environ.get('IMAGE_HOST')

# 初始化Notion客户端
notion = Client(auth=NOTION_API_KEY)

def get_page_blocks(page_id):
    """获取页面的所有blocks"""
    blocks = []
    cursor = None
    while True:
        response = notion.blocks.children.list(block_id=page_id, start_cursor=cursor)
        blocks.extend(response["results"])
        if not response["has_more"]:
            break
        cursor = response["next_cursor"]
    return blocks

def process_image_url(image_url):
    """通过图片处理服务处理图片URL"""
    if not IMAGE_HOST:
        raise ValueError("IMAGE_HOST is not set in environment variables")
    process_url = f"{IMAGE_HOST}/{image_url}"
    response = requests.get(process_url)
    response.raise_for_status()  # 确保请求成功
    result = json.loads(response.text)
    return result["url"]

def update_block_image(block_id, new_url):
    """更新block的图片URL"""
    notion.blocks.update(block_id, image={"external": {"url": new_url}})

def transfer_images(page_id):
    blocks = get_page_blocks(page_id)
    for block in blocks:
        if block["type"] == "image" and block["image"]["type"] == "external":
            image_url = block["image"]["external"]["url"]
            try:
                new_url = process_image_url(image_url)
                update_block_image(block["id"], new_url)
                logger.info(f"Successfully processed and updated image: {image_url} -> {new_url}")
            except Exception as e:
                logger.error(f"Failed to process image {image_url}: {str(e)}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        logger.error("Usage: python transfer_images.py <page_id>")
        sys.exit(1)
    
    page_id = sys.argv[1]
    if not NOTION_API_KEY:
        logger.error("NOTION_TOKEN is not set in environment variables")
    elif not IMAGE_HOST:
        logger.error("IMAGE_HOST is not set in environment variables")
    else:
        transfer_images(page_id)
