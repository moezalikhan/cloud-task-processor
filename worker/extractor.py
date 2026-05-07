import logging
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class ExtractionError(Exception):
    """Raised when URL extraction fails."""

def extract_metadata(url :str, timeout :int = 10) -> dict:
    url = str(url)
    # print(f"DEBUG: url type = {type(url)}, url value = {url}")
    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User_Agent": "cloud-task-processor/1.0"},
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ExtractionError(f"Failed to fetch {url}: {exc}") from exc

    soup = BeautifulSoup(response.text, "lxml")
    #  Extract the Title 
    title = None
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    #  Extract the Description
    description = None
    desc_tag = soup.find("meta", attrs = {"name":"description"})
    if desc_tag and desc_tag.get("content"):
        description = desc_tag["content"].strip()

    #   Extract word count  
    text = soup.get_text(separator = " ", strip = True)
    word_count = len(text.split())

    # Extracting the link count 
    link_count = len(soup.find_all("a", href=True))
   
    # Extracting the image count 
    img_count = len(soup.find_all("img" , src =True))

    return{
        "url" : url,
        "status_code": response.status_code,
        "title" : title,
        "description" : description,
        "word_count" : word_count,
        "link_count" : link_count,
        "image_count" : img_count,
    }