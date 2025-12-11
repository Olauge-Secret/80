import httpx
from typing import List, Optional
from pydantic import BaseModel
from app.config import settings
from app.utils.logging import AppLogger
from app.utils.jina_util import JinaUtil
from app.utils.openai_client import OpenAIClient
from app.utils.langfuse_client import langfuse_client
import app.constants as constants
import asyncio

logger = AppLogger().get_logger()

class SearchResult(BaseModel):
    title: str
    url: str
    
class CleanedContent(BaseModel):
    is_actual_content: bool
    cleaned_content: Optional[str] = None

class GoogleSearchClient:
    def __init__(self, langfuse_trace):
        self.base_url = "https://www.googleapis.com/customsearch/v1"
        self.params = {
            "key": settings.google_api_key,
            "cx": settings.google_cx_key,
        }
        self.langfuse_trace = langfuse_trace
        logger.info("GoogleSearchClient initialized")

    async def asearch(self, query: str, num_results: int = 5) -> List[SearchResult]:
        logger.info(f"Performing search for query: {query}, num_results: {num_results}")
        params = {**self.params, "q": query, "num": num_results}
        
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(self.base_url, params=params)
                response.raise_for_status()
                search_results = response.json()
                # logger.info(f"search_results: {search_results}")
            except httpx.HTTPStatusError as e:
                logger.error(f"HTTP error occurred: {e}")
                return []
            except Exception as e:
                logger.error(f"An error occurred during Google search: {e}")
                return []

        items = search_results.get('items', [])
        results = [SearchResult(title=item['title'], url=item['link']) for item in items]

        logger.info(f"Search completed. Found {len(results)} results.")
        return results
    
    async def get_body_text(self, url: str, llm_cleanup: bool = True, max_words: int = 12000) -> str:
        try:
            openai_client = OpenAIClient()
            openai_client.langfuse_trace = self.langfuse_trace
            response = await JinaUtil().afetch(url=url)
            
            content = response.get("content", "")
            
            word_count = len(content.split())
            
            if llm_cleanup and word_count > max_words:
                logger.info(f"Content for URL {url} has {word_count} words. Exceeds max_words {max_words}. Fetching again with Jina in text format.")
                response = await JinaUtil().afetch(url=url, return_format="text")
                return response.get("text", "")
            
            if llm_cleanup and len(content) > 0:
                cleaned_content = await openai_client.ainvoke_json(
                    output_pydantic_model=CleanedContent,
                    name="clean-markdown-content",
                    langfuse_prompt_args={"name": "clean-markdown-content"},
                    messages=[
                        {
                            "role": "user",
                            "content": langfuse_client.get_prompt_str(name="clean-markdown-content").format(content=content)
                        }
                    ],
                    model=langfuse_client.get_prompt_model_name(name="clean-markdown-content"),
                    timeout=360
                )
                if cleaned_content.get("is_actual_content", False) == True:
                    return cleaned_content.get("cleaned_content", "")
                
                return None
            
            return content
        except Exception as e:
            logger.error(f"Error in get_body_text for URL {url}: {e}")
            return None
    
    async def search_many(self, queries: List[str], num_results: int = 5) -> List[SearchResult]:
        """
        Execute multiple search queries in parallel.
        
        Args:
            queries (List[str]): List of search queries to execute
            num_results (int): Number of results to fetch per query
            
        Returns:
            List[SearchResult]: Combined list of search results from all queries
        """
        logger.info(f"Starting search for {len(queries)} queries, {num_results} results each")
        
        # Create tasks for all searches
        search_results = []
        for query in queries:
            search_results.append(await self.asearch(query, num_results))
        
        # Flatten the list of results and remove duplicates by URL
        seen_urls = set()
        unique_results = []
        
        for results in search_results:
            for result in results:
                if result.url not in seen_urls:
                    seen_urls.add(result.url)
                    unique_results.append(result)
        
        logger.info(f"Completed search with {len(unique_results)} unique results")
        return unique_results