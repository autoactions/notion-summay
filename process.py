import requests
import os
import logging
import sys
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import re
import time

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [PID %(process)d] - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 直接从环境变量获取Notion Token和摘要API
NOTION_TOKEN = os.environ.get('NOTION_TOKEN')
NOTION_API_BASE = 'https://api.notion.com/v1'
SUMMARY_API = os.environ.get('SUMMARY_API')

# 添加图床API的URL
IMAGE_API = os.environ.get('IMAGE_API')

# 创建一个自定义的会话
def create_session():
    session = requests.Session()
    retry = Retry(total=5, backoff_factor=0.1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session

session = create_session()

def notion_api_request(endpoint, method='GET', data=None):
    headers = {
        'Authorization': f'Bearer {NOTION_TOKEN}',
        'Notion-Version': '2022-06-28',
        'Content-Type': 'application/json'
    }
    url = f'{NOTION_API_BASE}{endpoint}'
    try:
        response = session.request(method, url, headers=headers, json=data)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        logger.error(f"Notion API请求失败: {e}")
        logger.error(f"请求URL: {url}")
        logger.error(f"请求方法: {method}")
        logger.error(f"请求数据: {data}")
        logger.error(f"响应状态码: {e.response.status_code}")
        logger.error(f"响应内容: {e.response.text}")
        raise
    except Exception as e:
        logger.error(f"Notion API请求时发生未知错误: {str(e)}")
        raise

def get_page_content(url):
    markdown_url = f"https://r.jina.ai/{url}"
    markdown_response = requests.get(markdown_url, timeout=60)
    if markdown_response.status_code == 200:
        return markdown_response.text
    else:
        logger.error(f"转换摘要为Markdown失败: {markdown_response.status_code}")

def get_summary_url(url):
    try:
        summary_url = f"{SUMMARY_API}/{url}"
        logger.info(f"生成摘要URL: {summary_url}")
        
        response = requests.get(summary_url, allow_redirects=False, timeout=60)
        if response.status_code == 302:
            redirect_url = response.headers.get('Location')
            logger.info(f"重定向后的摘要URL: {redirect_url}")
            return redirect_url
        else:
            logger.error(f"生成摘要失败: {response.status_code}")
    except requests.RequestException as e:
        logger.error(f"请求异常: {str(e)}")
    except Exception as e:
        logger.error(f"生成摘要时发生未知错误: {str(e)}")
    return None

def upload_image_to_cdn(image_url):
    """上传图片到CDN并返回新的URL"""
    logger.info(f"开始上传图片到CDN: {image_url}")
    try:
        # 确保image_url是完整的URL
        if not image_url.startswith(('http://', 'https://')):
            image_url = f'https://{image_url}'
        
        response = requests.get(f"{IMAGE_API}/{image_url}")
        response.raise_for_status()
        data = response.json()
        
        # 验证返回的URL
        if 'url' not in data or not data['url'].startswith('https://'):
            logger.warning(f"CDN返回的URL无效: {data}")
            return image_url  # 返回原始URL
        
        logger.info(f"成功上传图片到CDN,新URL: {data['url']}")
        return data['url']
    except requests.exceptions.RequestException as e:
        logger.error(f"上传图片到CDN时发生请求错误: {str(e)}")
        logger.error(f"请求URL: {IMAGE_API}/{image_url}")
        logger.error(f"响应状态码: {e.response.status_code if e.response else 'N/A'}")
        logger.error(f"响应内容: {e.response.text if e.response else 'N/A'}")
    except Exception as e:
        logger.error(f"上传图片到CDN时发生未知错误: {str(e)}")
    return image_url  # 如果上传失败,返回原始URL

def markdown_to_notion_blocks(markdown_text):
    blocks = []
    lines = markdown_text.split('\n')
    code_block = []
    in_code_block = False
    list_stack = []
    in_quote = False
    quote_content = []
    
    def create_text_block(text, annotations=None):
        block = {"type": "text", "text": {"content": text}}
        if annotations:
            block["annotations"] = annotations
        return block
    
    def create_paragraph_block(text_blocks):
        return {
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": text_blocks}
        }
    
    def process_inline_elements(line):
        parts = re.split(r'(\*\*.*?\*\*|\*.*?\*|__.*?__|_.*?_|`.*?`|~~.*?~~|\[.*?\]\(.*?\))', line)
        text_blocks = []
        for part in parts:
            if part.startswith('**') and part.endswith('**'):
                text_blocks.append(create_text_block(part[2:-2], {"bold": True}))
            elif part.startswith('*') and part.endswith('*'):
                text_blocks.append(create_text_block(part[1:-1], {"italic": True}))
            elif part.startswith('__') and part.endswith('__'):
                text_blocks.append(create_text_block(part[2:-2], {"bold": True}))
            elif part.startswith('_') and part.endswith('_'):
                text_blocks.append(create_text_block(part[1:-1], {"italic": True}))
            elif part.startswith('`') and part.endswith('`'):
                text_blocks.append(create_text_block(part[1:-1], {"code": True}))
            elif part.startswith('~~') and part.endswith('~~'):
                text_blocks.append(create_text_block(part[2:-2], {"strikethrough": True}))
            elif part.startswith('[') and '](' in part and part.endswith(')'):
                text, url = re.match(r'\[(.*?)\]\((.*?)\)', part).groups()
                text_blocks.append({"type": "text", "text": {"content": text, "link": {"url": url}}})
            else:
                text_blocks.append(create_text_block(part))
        return text_blocks
    
    for line in lines:
        # 处理代码块
        if line.startswith('```'):
            if in_code_block:
                language = code_block[0].strip('`').lower() or "plain_text"
                code_content = '\n'.join(code_block[1:])
                blocks.append({
                    "object": "block",
                    "type": "code",
                    "code": {
                        "rich_text": [{"type": "text", "text": {"content": code_content}}],
                        "language": language
                    }
                })
                in_code_block = False
                code_block = []
            else:
                in_code_block = True
            continue
        
        if in_code_block:
            code_block.append(line)
            continue
        
        # 处理引用
        if line.strip().startswith('> '):
            content = line.strip()[2:].strip()
            if not in_quote:
                in_quote = True
                quote_content = [content]
            else:
                quote_content.append(content)
            continue
        elif in_quote:
            blocks.append({
                "object": "block",
                "type": "quote",
                "quote": {
                    "rich_text": process_inline_elements('\n'.join(quote_content))
                }
            })
            in_quote = False
            quote_content = []
        
        # 处理标题
        if line.startswith('#'):
            level = min(len(line.split()[0]), 3)  # Notion只支持到h3
            content = line.lstrip('#').strip()
            blocks.append({
                "object": "block",
                "type": f"heading_{level}",
                f"heading_{level}": {
                    "rich_text": process_inline_elements(content),
                    "color": "default"
                }
            })
            list_stack = []
        
        # 处理任务列表
        elif line.strip().startswith('- [ ] ') or line.strip().startswith('- [x] '):
            checked = line.strip().startswith('- [x] ')
            content = line.strip()[6:].strip()
            blocks.append({
                "object": "block",
                "type": "to_do",
                "to_do": {
                    "rich_text": process_inline_elements(content),
                    "checked": checked,
                    "color": "default"
                }
            })
            list_stack = [("to_do", 0)]
        
        # 处理无序列表
        elif line.strip().startswith(('- ', '* ', '+ ')):
            indent = len(line) - len(line.lstrip())
            content = line.strip()[2:].strip()
            
            while list_stack and list_stack[-1][1] >= indent:
                list_stack.pop()
            
            if list_stack:
                parent_block = list_stack[-1][0]
                last_block = blocks[-1]
                if "children" not in last_block[parent_block]:
                    last_block[parent_block]["children"] = []
                last_block[parent_block]["children"].append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": process_inline_elements(content),
                        "color": "default"
                    }
                })
                list_stack.append((last_block[parent_block]["children"][-1], indent))
            else:
                blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": process_inline_elements(content),
                        "color": "default"
                    }
                })
                list_stack.append((blocks[-1]["bulleted_list_item"], indent))
        
        # 处理有序列表
        elif re.match(r'^\s*\d+\.\s', line):
            indent = len(line) - len(line.lstrip())
            content = re.sub(r'^\s*\d+\.\s', '', line).strip()
            
            while list_stack and list_stack[-1][1] >= indent:
                list_stack.pop()
            
            if list_stack:
                parent_block = list_stack[-1][0]
                last_block = blocks[-1]
                if "children" not in last_block[parent_block]:
                    last_block[parent_block]["children"] = []
                last_block[parent_block]["children"].append({
                    "object": "block",
                    "type": "numbered_list_item",
                    "numbered_list_item": {
                        "rich_text": process_inline_elements(content),
                        "color": "default"
                    }
                })
                list_stack.append((last_block[parent_block]["numbered_list_item"], indent))
            else:
                blocks.append({
                    "object": "block",
                    "type": "numbered_list_item",
                    "numbered_list_item": {
                        "rich_text": process_inline_elements(content),
                        "color": "default"
                    }
                })
                list_stack.append((blocks[-1]["numbered_list_item"], indent))
        
        # 处理分割线
        elif line.strip() in ['---', '***', '___']:
            blocks.append({"object": "block", "type": "divider", "divider": {}})
            list_stack = []
        
        # 处理图片
        elif line.strip().startswith('!['):
            match = re.match(r'!\[(.*?)\]\((.*?)\)', line.strip())
            if match:
                alt_text, image_url = match.groups()
                logger.info(f"处理图片: 原始URL={image_url}")
                # 上传图片到CDN
                cdn_url = upload_image_to_cdn(image_url)
                # 确保URL是https的
                if not cdn_url.startswith('https://'):
                    cdn_url = f'https://{cdn_url.lstrip("http://")}'
                logger.info(f"最终使用的图片URL: {cdn_url}")
                blocks.append({
                    "object": "block",
                    "type": "image",
                    "image": {
                        "type": "external",
                        "external": {"url": cdn_url}
                    }
                })
            list_stack = []
        
        # 处理普通段落
        else:
            if line.strip():
                if list_stack:
                    # 处理列表项的子段落
                    last_block = blocks[-1]
                    if last_block["type"] in ["bulleted_list_item", "numbered_list_item"]:
                        last_block[last_block["type"]]["children"] = last_block[last_block["type"]].get("children", [])
                        last_block[last_block["type"]]["children"].append(
                            create_paragraph_block(process_inline_elements(line.strip()))
                        )
                else:
                    blocks.append(create_paragraph_block(process_inline_elements(line)))
            else:
                list_stack = []
    
    # 处理文档末尾的未闭合引用
    if in_quote:
        blocks.append({
            "object": "block",
            "type": "quote",
            "quote": {
                "rich_text": process_inline_elements('\n'.join(quote_content))
            }
        })
    
    return blocks

def process_notion_page(page_id):
    """处理单个Notion页面"""
    try:
        logger.info(f"开始处理Notion页面: {page_id}")
        page = notion_api_request(f'/pages/{page_id}')
        url = page['properties'].get('原链接', {}).get('url')
        title = page['properties'].get('标题', {}).get('title', [{}])[0].get('plain_text', '')
        
        if not url:
            logger.warning(f"页面 {page_id} 缺少有效的原始链接,跳过处理")
            return
        
        logger.info(f"获取页面 {page_id} 的摘要内容")
        page_content = get_page_content(url)
        if not page_content:
            logger.warning(f"页面 {page_id} 未能生成有效的摘要Markdown,跳过处理")
            return
        
        logger.info(f"为页面 {page_id} 创建新的摘要子页面")
        new_page = notion_api_request('/pages', 'POST', {
            "parent": {"page_id": page_id},
            "properties": {
                "title": {"title": [{"text": {"content": f"{title} - 摘要"}}]}
            }
        })
        
        logger.info(f"将摘要内容转换为Notion块")
        blocks = markdown_to_notion_blocks(page_content)
        
        logger.info(f"开始向新页面 {new_page['id']} 添加内容块")
        for i in range(0, len(blocks), 100):
            batch = blocks[i:i+100]
            retry_count = 0
            while retry_count < 3:  # 最多重试3次
                try:
                    notion_api_request(f'/blocks/{new_page["id"]}/children', 'PATCH', {"children": batch})
                    logger.info(f"成功添加第 {i//100 + 1} 批内容块")
                    break
                except Exception as e:
                    retry_count += 1
                    logger.error(f"添加第 {i//100 + 1} 批内容块时发生错误 (尝试 {retry_count}/3): {str(e)}")
                    if retry_count == 3:
                        logger.error(f"无法添加第 {i//100 + 1} 批内容块,跳过此批次")
                        logger.error(f"失败的内容块: {batch}")
                    else:
                        time.sleep(1)  # 在重试之前等待1秒
        
        logger.info(f"更新原始页面 {page_id} 的笔记属性")
        notion_api_request(f'/pages/{page_id}', 'PATCH', {
            "properties": {
                "笔记": {"url": get_summary_url(url)}
            }
        })
        
        logger.info(f"页面 {page_id} 处理完成")
    except Exception as e:
        logger.error(f"处理页面 {page_id} 时发生未捕获的错误: {str(e)}")

def main(page_ids):
    """主函数,处理多个Notion页面"""
    logger.info(f"开始处理 {len(page_ids)} 个Notion页面")
    for index, page_id in enumerate(page_ids, 1):
        logger.info(f"处理第 {index}/{len(page_ids)} 个页面: {page_id}")
        process_notion_page(page_id)
    logger.info("所有页面处理完成")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.error("使用方法: python process.py <page_id1> [page_id2 ...]")
        sys.exit(1)
    
    page_ids = sys.argv[1:]
    main(page_ids)
