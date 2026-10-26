from langchain.tools import tool
import requests
from tavily import TavilyClient
from bs4 import BeautifulSoup
import os
from dotenv import load_dotenv
from rich import print

load_dotenv()

tavily_client = TavilyClient(
    api_key=os.getenv("TAVILY_API_KEY")
)

@tool
def web_search(query: str) -> str:
    """Search the web for recent and reliable information on a given topic. Returns titles, URLs and snippets."""

    results = tavily_client.search(
        query=query,
        max_results=5
    )

    out =[]
    for r in results['results']:
        out.append(
            f"Title: {r['title']}\nURL:  {r['url']}\nSnippet: {r['content'][:300]}\n"
        )
    return "\n----\n".join(out)

print(web_search.invoke("latest advancements in AI technology"))

@tool 
def scrape_url(url: str)-> str:
    """Scrape and return clean text content from a given URL for deeper reading."""
    
    try:
        resp = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()
            
        return soup.get_text(separator="\n", strip=True)[:3000]
    except Exception as e:
        return f"could not scrape the URL: {e}"
    