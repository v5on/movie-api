from fastapi import FastAPI, HTTPException
import httpx
from bs4 import BeautifulSoup
import re
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/search")
async def search_movies(query: str):
    """Step 1: Search for movies on MovieLinkHub"""
    try:
        async with httpx.AsyncClient() as client:
            # Search for the movie
            search_url = f"https://movielinkhub.fun/?s={query}"
            response = await client.get(search_url)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract search results
            results = []
            for item in soup.select('.result-item'):
                title_tag = item.select_one('.title a')
                if not title_tag:
                    continue

                result = {
                    "title": title_tag.get_text(strip=True),
                    "url": title_tag['href'],
                    "year": item.select_one('.year').get_text(strip=True) if item.select_one('.year') else "N/A",
                    "type": item.select_one('.movies').get_text(strip=True) if item.select_one('.movies') else "Unknown",
                    "description": item.select_one('.contenido p').get_text(strip=True) if item.select_one('.contenido p') else "",
                    "thumbnail": item.select_one('img')['src'] if item.select_one('img') else ""
                }
                results.append(result)

            return {"query": query, "results": results}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def parse_quality(quality_str: str) -> int:
    """Parses a quality string (e.g., '1080p') into an integer."""
    if not quality_str:
        return 0
    match = re.search(r'(\d+)', quality_str)
    if match:
        return int(match.group(1))
    return 0

@app.get("/api/download-links")
async def get_download_links(url: str):
    """Step 2: Get download links from movie page, selecting the best quality."""
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            # Get the movie page
            response = await client.get(url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Find all download link sections and select the best quality
            download_blocks = soup.select("tr[id^='link-']")
            best_link = None
            max_quality = -1
            selected_block_html = ""

            for block in download_blocks:
                quality_tag = block.select_one('.qua')
                quality_text = quality_tag.get_text(strip=True) if quality_tag else ""
                current_quality = parse_quality(quality_text)

                if current_quality > max_quality:
                    link_tag = block.select_one("a[href*='/links/']")
                    if link_tag:
                        max_quality = current_quality
                        best_link = link_tag['href']
                        selected_block_html = str(block)

            # Fallback if the primary method fails to find a link
            if not best_link:
                button = soup.select_one("button.downbtn")
                if button:
                    link_tag = button.find_parent('a')
                    if link_tag and link_tag.has_attr('href'):
                        best_link = link_tag['href']

            if not best_link:
                raise HTTPException(status_code=404, detail="Download link not found on the page.")

            download_page_url = best_link

            # Get the intermediate download page
            download_response = await client.get(download_page_url)
            download_response.raise_for_status()

            # Extract the final redirect URL from the script or meta tag
            final_url_match = re.search(r'https?://linkedmoviehub\.top[^\s\'"]+', download_response.text)
            if not final_url_match:
                raise HTTPException(status_code=404, detail="Final download page URL not found on intermediate page.")
            
            final_url = final_url_match.group(0)

            # Extract quality, size, and language info from the selected block on the movie page
            quality_info = {
                "quality": "Unknown",
                "size": "Unknown",
                "language": "Unknown"
            }
            
            if selected_block_html:
                quality_match = re.search(r'class=[\'"]qua[\'"]>([^<]+)', selected_block_html)
                size_match = re.search(r'class=[\'"]siz[\'"]>\[([^\]]+)', selected_block_html)
                lang_match = re.search(r'class=[\'"]lan[\'"]>\(([^\)]+)', selected_block_html)

                if quality_match:
                    quality_info["quality"] = quality_match.group(1).strip()
                if size_match:
                    quality_info["size"] = size_match.group(1).strip()
                if lang_match:
                    quality_info["language"] = lang_match.group(1).strip()

            return {
                "intermediate_page_url": download_page_url,
                "final_page_url": final_url,
                "selected_quality_info": quality_info
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/final-links")
async def get_final_download_links(url: str):
    """Step 3: Get all download options from the final page"""
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            # Get the final download page
            response = await client.get(url)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # Organize links by quality
            quality_sections = {}

            # Find all quality sections
            for quality_div in soup.select('div.quality'):
                quality = quality_div.find('h2').get_text(strip=True)

                # Get all download buttons in this section
                links = []
                for link in quality_div.find_next('center').select('a.down-btn'):
                    provider = link.get_text(strip=True)
                    url = link['href']
                    links.append({
                        "provider": provider,
                        "url": url
                    })

                if links:
                    quality_sections[quality] = links

            if not quality_sections:
                raise HTTPException(status_code=404, detail="No download links found")

            return quality_sections

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/src")
async def search_and_get_all_links(query: str):
    """Combined endpoint to search for a movie and get all download links."""
    try:
        # Step 1: Search for movies
        search_response = await search_movies(query)
        search_results = search_response.get("results", [])

        if not search_results:
            return {
                "ok": True,
                "developer": "Tofazzal Hossain",
                "results": []
            }

        final_results = []
        async with httpx.AsyncClient(follow_redirects=True) as client:
            for movie in search_results:
                movie_url = movie.get("url")
                if not movie_url:
                    continue

                try:
                    # Step 2: Get the final download page URL
                    download_page_info = await get_download_links(movie_url)
                    final_page_url = download_page_info.get("final_page_url")

                    if not final_page_url:
                        continue

                    # Step 3: Get the final download links
                    final_links_by_quality = await get_final_download_links(final_page_url)

                    # Format the links as requested
                    download_links_formatted = []
                    for quality, links_list in final_links_by_quality.items():
                        urls = [link["url"] for link in links_list]
                        download_links_formatted.append({
                            "quality": quality,
                            "links": urls
                        })
                    
                    # Combine all information
                    final_results.append({
                        "title": movie.get("title"),
                        "year": movie.get("year"),
                        "type": movie.get("type"),
                        "poster": movie.get("thumbnail"),
                        "downloadLink": download_links_formatted
                    })

                except HTTPException as e:
                    # If one movie fails, print an error and continue with the next
                    print(f"Failed to process movie '{movie.get('title')}': {e.detail}")
                    continue
        
        return {
            "ok": True,
            "developer": "Mahir Labib",
            "results": final_results
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
