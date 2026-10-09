"""TeraBox API client module.

This module handles all interactions with the TeraBox API,
including fetching file information, download links, and formatting responses.
"""

import asyncio
import logging
import re
from typing import Any, Dict, List, Optional, Union
from urllib.parse import parse_qs, urlparse

import aiohttp

from .config import ALLOWED_HOSTS, headers, load_cookies
from .utils import find_between, extract_thumbnail_dimensions, get_formatted_size, request_with_retry


class FileList(list):
    """Subclass of list to carry additional metadata like fallback_no_cookie."""
    fallback_no_cookie: bool = False
    used_cookies: bool = False


_DIRECT_UPSTREAM_DOMAINS = (
    "www.1024tera.com",
    "1024tera.com",
    "www.1024terabox.com",
    "1024terabox.com",
    "www.terabox.com",
    "www.terabox.app",
    "terabox.app",
)
_JS_TOKEN_PATTERN = re.compile(
    r"""fn(?:%28|\()(?:%22|%27|["'])([^%'"()]+)(?:%22|%27|["'])(?:%29|\))"""
)


def _cookie_header(cookies: Dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in cookies.items() if value)


def _share_list_api_url(domain: str, cookies: dict[str, str]) -> str:
    if cookies.get("TSID") or "1024tera.com" in domain.lower():
        return "https://dm.1024tera.com/share/list"
    return "https://dm.terabox.app/share/list"


async def _fetch_direct_file_list(
    surl: str,
    cookies: dict[str, str],
    preferred_domain: str,
    password: str = "",
    directory_path: str = "",
) -> Union[FileList, Dict[str, Any]]:
    """Resolve a share directly from TeraBox when the configured Worker fails."""
    if cookies.get("TSID"):
        candidates = ["www.1024tera.com", "1024tera.com", preferred_domain, *_DIRECT_UPSTREAM_DOMAINS]
    else:
        candidates = [preferred_domain, *_DIRECT_UPSTREAM_DOMAINS]
    domains = list(dict.fromkeys(
        domain for domain in candidates if domain in ALLOWED_HOSTS
    ))[:3]
    last_message = "No supported TeraBox upstream domains were available"

    try:
        connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
        async with aiohttp.ClientSession(
            connector=connector,
            cookies=cookies,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=25, connect=10),
        ) as session:
            for domain in domains:
                page_url = f"https://{domain}/sharing/link"
                page_headers = {"Accept": "text/html", "Referer": f"https://{domain}/"}
                cookie_header = _cookie_header(cookies)
                if cookie_header:
                    page_headers["Cookie"] = cookie_header
                try:
                    async with request_with_retry(
                        session,
                        "GET",
                        page_url,
                        params={"surl": surl},
                        headers=page_headers,
                        max_retries=0,
                    ) as page_response:
                        if page_response.status != 200:
                            last_message = f"Share page returned HTTP {page_response.status} on {domain}"
                            continue
                        html = await page_response.text()

                    if "need verify" in html.lower():
                        return {
                            "error": "TeraBox verification required",
                            "errno": -1,
                            "message": f"Complete the verification in a browser, then refresh the local cookies ({domain})",
                            "requires_password": False,
                            "details": {"code": "verification_required", "domain": domain},
                        }

                    token_match = _JS_TOKEN_PATTERN.search(html)
                    if not token_match or len(html) <= 500:
                        last_message = f"Could not extract jsToken from the share page on {domain}"
                        continue

                    api_params = {
                        "jsToken": token_match.group(1),
                        "shorturl": surl,
                        "root": "0" if directory_path else "1",
                    }
                    if directory_path:
                        api_params.update({
                            "dir": directory_path,
                            "order": "asc",
                            "by": "name",
                        })
                    if password:
                        api_params["pwd"] = password
                    api_headers = {
                        "Accept": "application/json",
                        "Referer": f"https://{domain}/",
                    }
                    if cookie_header:
                        api_headers["Cookie"] = cookie_header
                    async with request_with_retry(
                        session,
                        "GET",
                        _share_list_api_url(domain, cookies),
                        params=api_params,
                        headers=api_headers,
                        max_retries=0,
                    ) as api_response:
                        if api_response.status != 200:
                            last_message = f"TeraBox share API returned HTTP {api_response.status}"
                            continue
                        api_data = await api_response.json(content_type=None)

                    errno = str(api_data.get("errno", "-1"))
                    if errno in ("400141", "4000020"):
                        return {
                            "error": "Verification required",
                            "errno": int(errno),
                            "message": api_data.get("errmsg", "This share requires verification"),
                            "requires_password": True,
                        }
                    if errno != "0":
                        last_message = api_data.get("errmsg", f"TeraBox API error {errno}")
                        continue

                    files = api_data.get("list")
                    if isinstance(files, list) and files:
                        if not directory_path and str(files[0].get("isdir")) == "1" and files[0].get("path"):
                            directory_params = {
                                **api_params,
                                "dir": files[0]["path"],
                                "order": "asc",
                                "by": "name",
                            }
                            async with request_with_retry(
                                session,
                                "GET",
                                _share_list_api_url(domain, cookies),
                                params=directory_params,
                                headers=api_headers,
                                max_retries=0,
                            ) as directory_response:
                                if directory_response.status == 200:
                                    directory_data = await directory_response.json(content_type=None)
                                    directory_files = directory_data.get("list")
                                    if (
                                        str(directory_data.get("errno")) == "0"
                                        and isinstance(directory_files, list)
                                        and directory_files
                                    ):
                                        files = directory_files

                        result = FileList(files)
                        result.used_cookies = bool(cookies)
                        return result
                    last_message = "TeraBox returned an empty share list"
                except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError):
                    last_message = f"Direct request failed on {domain}"
    except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError):
        last_message = "Could not connect to TeraBox directly"

    return {
        "error": "Direct TeraBox resolution failed",
        "errno": -1,
        "message": last_message,
        "details": {"source": "direct", "domains_tried": domains},
    }


async def fetch_download_link(
    url: str,
    password: str = "",
    cookies: Optional[Dict[str, str]] = None,
    directory_path: str = "",
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Fetch file information from TeraBox share link using unified proxy API.
    
    This function tries the unified Worker first, then D1 lookup and a bounded
    direct TeraBox request if the Worker cannot extract jsToken. Verification
    challenges are returned to the caller and are never bypassed.
    
    Args:
        url: TeraBox share URL
        password: Optional password for protected links
        
    Returns:
        Union[List[Dict[str, Any]], Dict[str, Any]]: List of files or error dict
    """
    try:
        from .config import PROXY_BASE_URL, PROXY_MODE_LOOKUP, PROXY_MODE_RESOLVE
        
        # Extract surl from URL
        parsed_url = urlparse(url)
        if "surl=" in parsed_url.query:
            surl = parse_qs(parsed_url.query)["surl"][0]
        elif "/s/" in parsed_url.path:
            surl = parsed_url.path.split("/s/")[1].split("/")[0].split("?")[0]
        else:
            logging.error("Could not extract surl from URL")
            return {"error": "Invalid URL format", "errno": -1}
        
        # Remove leading "1" if present (TeraBox shortcode format)
        if surl.startswith("1"):
            surl = surl[1:]
        
        # We will attempt with loaded cookies first, and retry without cookies if blocked by verification
        initial_cookies = cookies if cookies is not None else load_cookies()
        if directory_path:
            return await _fetch_direct_file_list(
                surl,
                initial_cookies,
                parsed_url.hostname.lower() if parsed_url.hostname else "",
                password,
                directory_path,
            )
        attempts = [initial_cookies]
        # Only add a retry without cookies if we actually have cookies to test with initially
        if initial_cookies:
            attempts.append({})
            
        for idx, cookies_to_send in enumerate(attempts):
            is_last_attempt = (idx == len(attempts) - 1)
            try:
                # Use unified proxy with mode=resolve for automatic token extraction and API call
                connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
                async with aiohttp.ClientSession(connector=connector, cookies=cookies_to_send, headers=headers) as session:
                    params = {
                        "mode": PROXY_MODE_RESOLVE,
                        "surl": surl,
                        "domain": parsed_url.hostname.lower() if parsed_url.hostname else "",
                        "raw": "1",  # Get raw upstream response instead of simplified format
                    }
                    if password:
                        params["pwd"] = password
                    
                    logging.info(f"Fetching file list from unified proxy (mode=resolve, attempt={idx+1}): {PROXY_BASE_URL}")
                    
                    async with request_with_retry(session, "GET", PROXY_BASE_URL, params=params) as response:
                        # Handle non-200 responses
                        if response.status != 200:
                            error_text = await response.text()
                            logging.error(f"Proxy returned {response.status}: {error_text}")
                            
                            should_retry = False
                            err_json = None
                            try:
                                import json
                                err_json = json.loads(error_text)
                                worker_code = err_json.get("code", "")
                                if worker_code == "token_extract_failed_all":
                                    try:
                                        lookup_params = {"mode": PROXY_MODE_LOOKUP, "surl": surl}
                                        async with request_with_retry(
                                            session, "GET", PROXY_BASE_URL, params=lookup_params
                                        ) as lookup_response:
                                            if lookup_response.status == 200:
                                                lookup_result = await lookup_response.json()
                                                lookup_data = lookup_result.get("data", {})
                                                cached_files = (
                                                    lookup_data.get("list")
                                                    if isinstance(lookup_data, dict)
                                                    else None
                                                )
                                                if isinstance(cached_files, list) and cached_files:
                                                    logging.info(
                                                        "Resolved share from Worker D1 lookup after live extraction failed"
                                                    )
                                                    return FileList(cached_files)
                                    except Exception as lookup_error:
                                        logging.warning(
                                            f"Worker D1 lookup fallback failed: {lookup_error}"
                                        )

                                    try:
                                        direct_result = await _fetch_direct_file_list(
                                            surl,
                                            initial_cookies,
                                            parsed_url.hostname.lower() if parsed_url.hostname else "",
                                            password,
                                        )
                                        if isinstance(direct_result, FileList):
                                            logging.info("Resolved share directly from TeraBox after Worker failure")
                                            return direct_result
                                        direct_details = direct_result.get("details", {})
                                        if (
                                            isinstance(direct_details, dict)
                                            and direct_details.get("code") == "verification_required"
                                        ):
                                            return direct_result
                                        err_json["local_fallback"] = direct_result
                                    except Exception as direct_error:
                                        logging.warning(
                                            "Direct TeraBox fallback failed (%s)",
                                            direct_error.__class__.__name__,
                                        )

                                upstream_err = err_json.get("details", {})
                                if isinstance(upstream_err, dict):
                                    upstream_errno = upstream_err.get("errno")
                                    upstream_msg = upstream_err.get("errmsg", "")
                                    upstream_reason = upstream_err.get("reason", "")
                                    worker_code = err_json.get("code", "")
                                    if (
                                        upstream_errno == 4000020
                                        or "need verify" in str(upstream_msg).lower()
                                    ):
                                        if not is_last_attempt:
                                            logging.warning("Upstream returned 'need verify' with cookies. Retrying without cookies.")
                                            should_retry = True
                                        else:
                                            return {
                                                "error": err_json.get("error", "Verification required"),
                                                "errno": upstream_errno if upstream_errno is not None else -1,
                                                "message": upstream_reason or upstream_msg or "TeraBox returned a verification challenge",
                                                "surl": surl,
                                                "requires_password": upstream_errno in (4000020, 400141),
                                                "details": err_json,
                                            }
                            except Exception as e:
                                logging.debug(f"Failed to parse error response JSON: {e}")
                            
                            if should_retry:
                                continue
                            
                            if isinstance(err_json, dict):
                                upstream_details = err_json.get("details")
                                if isinstance(upstream_details, dict):
                                    error_message = upstream_details.get("reason") or err_json.get("error", "")
                                else:
                                    error_message = upstream_details or err_json.get("error", "")
                                details = err_json
                            else:
                                error_message = error_text[:200]
                                details = error_text[:200]

                            return {
                                "error": f"Proxy error: {response.status}",
                                "errno": -1,
                                "message": error_message,
                                "details": details,
                            }
                        
                        response_data = await response.json()
                        
                        # Check for error response from proxy
                        if "error" in response_data:
                            error_msg = response_data.get("error", "Unknown error")
                            logging.error(f"Proxy error: {error_msg}")
                            
                            # Check if this is a token extraction failure (may need cookies)
                            if "jsToken" in error_msg or "cookie" in error_msg.lower():
                                return {
                                    "error": error_msg,
                                    "errno": -1,
                                    "message": "Failed to extract authentication tokens. Cookies may be required for this share.",
                                }
                            
                            return {
                                "error": error_msg,
                                "errno": -1,
                            }
                        
                        # With raw=1, the response format is: {"source": "live", "upstream": {...}}
                        # Extract the actual TeraBox API response from upstream
                        if "upstream" in response_data:
                            api_response = response_data["upstream"]
                            logging.info(f"Proxy response source: {response_data.get('source', 'unknown')}")
                        else:
                            # Fallback for other formats
                            api_response = response_data.get("data", response_data)
                        
                        # Handle TeraBox API errors
                        errno = api_response.get("errno", -1)
                        logging.info(f"Response errno: {errno}")
                        
                        # Handle verification required
                        if errno == 400141 or errno == 4000020:
                            if not is_last_attempt:
                                logging.warning(f"Upstream returned errno {errno} with cookies. Retrying without cookies.")
                                continue
                            
                            logging.warning("Link requires verification")
                            return {
                                "error": "Verification required",
                                "errno": errno,
                                "message": "This link requires password or captcha verification",
                                "surl": surl,
                                "requires_password": True,
                            }
                        
                        # Handle other errors
                        if errno != 0:
                            error_msg = api_response.get("errmsg", "Unknown error")
                            logging.error(f"API error {errno}: {error_msg}")
                            return {"error": error_msg, "errno": errno}
                        
                        # Check if we got the file list
                        if "list" not in api_response:
                            logging.error(f"No file list in response. Response keys: {list(api_response.keys())}")
                            return {"error": "No files found in response", "errno": -1}
                        
                        files = api_response["list"]
                        logging.info(f"Found {len(files)} items")
                        
                        # If it's a directory (only in full API format), fetch its contents
                        if files and files[0].get("isdir") == "1":
                            logging.info("Fetching directory contents")
                            
                            # For directory contents, we need to use the API mode with additional parameters
                            # Extract necessary tokens from the initial response if available
                            js_token = api_response.get("jsToken")
                            log_id = api_response.get("dplogid")
                            
                            if not js_token:
                                logging.warning("No jsToken in response for directory listing, returning folder info only")
                                result_files = FileList(files)
                                result_files.fallback_no_cookie = (idx > 0)
                                result_files.used_cookies = (idx == 0 and bool(cookies_to_send))
                                return result_files
                            
                            # Use mode=api for directory contents with the jsToken
                            from .config import PROXY_MODE_API
                            
                            dir_params = {
                                "mode": PROXY_MODE_API,
                                "jsToken": js_token,
                                "shorturl": surl,
                                "dir": files[0]["path"],
                                "order": "asc",
                                "by": "name",
                            }
                            if log_id:
                                dir_params["dplogid"] = log_id
                            if password:
                                dir_params["pwd"] = password
                            
                            async with request_with_retry(session, "GET", PROXY_BASE_URL, params=dir_params) as dir_response:
                                if dir_response.status != 200:
                                    logging.warning("Failed to fetch directory contents, returning folder info")
                                    result_files = FileList(files)
                                    result_files.fallback_no_cookie = (idx > 0)
                                    result_files.used_cookies = (idx == 0 and bool(cookies_to_send))
                                    return result_files
                                
                                dir_data = await dir_response.json()
                                
                                # Handle wrapped response for directory listing too
                                if "data" in dir_data:
                                    dir_data = dir_data["data"]
                                
                                if "list" in dir_data and dir_data.get("errno") == 0:
                                    files = dir_data["list"]
                                    logging.info(f"Found {len(files)} files in directory")
                                else:
                                    logging.warning("Failed to parse directory contents, returning folder info")
                        
                        result_files = FileList(files)
                        result_files.fallback_no_cookie = (idx > 0)
                        result_files.used_cookies = (idx == 0 and bool(cookies_to_send))
                        return result_files
            except Exception as e:
                logging.error(f"Error on attempt {idx+1}: {e}", exc_info=True)
                if is_last_attempt:
                    return {"error": str(e), "errno": -1}
                    
    except Exception as e:
        logging.error(f"Unexpected error: {e}", exc_info=True)
        return {"error": str(e), "errno": -1}


async def format_file_info(file_data: Dict[str, Any]) -> Dict[str, Any]:
    """Format file information for API response.
    
    Args:
        file_data: Raw file data from TeraBox API
        
    Returns:
        Dict[str, Any]: Formatted file information
    """
    thumbnails = {}
    if "thumbs" in file_data:
        for key, url in file_data["thumbs"].items():
            if url:
                dimensions = extract_thumbnail_dimensions(url)
                thumbnails[dimensions] = url

    return {
        "filename": file_data.get("server_filename", "Unknown"),
        "size": get_formatted_size(file_data.get("size", 0)),
        "size_bytes": file_data.get("size", 0),
        "download_link": file_data.get("dlink", ""),
        "is_directory": file_data.get("isdir") == "1",
        "thumbnails": thumbnails,
        "path": file_data.get("path", ""),
        "fs_id": file_data.get("fs_id", ""),
    }


async def fetch_direct_links(
    url: str,
    password: str = "",
    files: Optional[List[Dict[str, Any]]] = None,
    cookies: Optional[Dict[str, str]] = None,
) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Fetch files with direct download links (alternative method).
    
    Args:
        url: TeraBox share URL
        password: Optional password for protected links
        files: Optional list of files already fetched from cache or API
        
    Returns:
        Union[List[Dict[str, Any]], Dict[str, Any]]: List of files with direct links or error dict
    """

    try:
        if files is None:
            files = await fetch_download_link(url, password)

        if isinstance(files, dict) and "error" in files:
            return files

        # Load cookies for the session (previous code referenced undefined `cookies`)
        session_cookies = cookies if cookies is not None else load_cookies()
        cookie_header = _cookie_header(session_cookies)

        connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
        async with aiohttp.ClientSession(
            connector=connector,
            cookies=session_cookies,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30, connect=10),
        ) as session:
            results = FileList()
            if hasattr(files, "fallback_no_cookie"):
                results.fallback_no_cookie = files.fallback_no_cookie
            if hasattr(files, "used_cookies"):
                results.used_cookies = files.used_cookies
            for item in files or []:
                # Ensure each item is a dict; skip otherwise

                if not isinstance(item, dict):
                    logging.warning(f"Skipping non-dict item in files: {type(item)}")

                    continue

                # Get direct link by following redirect

                dlink = item.get("dlink") or ""
                logging.info(f"Direct link: {dlink}")

                direct_link = None

                if dlink:
                    try:
                        async with request_with_retry(
                            session,
                            "HEAD",
                            dlink,
                            allow_redirects=False,
                            **({"headers": {"Cookie": cookie_header}} if cookie_header else {}),
                        ) as response:
                            direct_link = response.headers.get("Location")

                    except Exception as e:
                        logging.error(f"Error getting direct link: {e}")

                results.append(
                    {
                        "filename": item.get("server_filename", "Unknown"),
                        "size": get_formatted_size(item.get("size", 0)),
                        "size_bytes": item.get("size", 0),
                        "link": dlink,
                        "direct_link": direct_link,
                        "thumbnail": (item.get("thumbs") or {}).get("url3", ""),
                        "is_directory": item.get("isdir") in ("1", 1),
                        "path": item.get("path", ""),
                        "fs_id": item.get("fs_id", ""),
                    }
                )

            return results

    except Exception as e:
        logging.error(f"Error in fetch_direct_links: {e}")

        return {"error": str(e), "errno": -1}


async def _gather_format_file_info(files: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Helper to run format_file_info concurrently for a list of file dicts.
    
    Args:
        files: List of file data dictionaries
        
    Returns:
        List[Dict[str, Any]]: List of formatted file information
    """
    tasks = [format_file_info(item) for item in files if isinstance(item, dict)]
    if not tasks:
        return []
    results = await asyncio.gather(*tasks)
    return results


async def _normalize_api2_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize items returned by fetch_direct_links to the /api response shape.
    
    Args:
        items: List of items from fetch_direct_links
        
    Returns:
        List[Dict[str, Any]]: Normalized list of file information
    """
    out = FileList()
    if hasattr(items, "fallback_no_cookie"):
        out.fallback_no_cookie = items.fallback_no_cookie
    if hasattr(items, "used_cookies"):
        out.used_cookies = items.used_cookies
    for item in items or []:
        try:
            if not isinstance(item, dict):
                continue
            filenamestr = item.get("filename") or item.get("server_filename", "Unknown")
            size_h = (
                item.get("size")
                if isinstance(item.get("size"), str)
                else get_formatted_size(item.get("size", 0))
            )
            size_b = item.get("size_bytes", item.get("size", 0))
            download = (
                item.get("direct_link")
                or item.get("download_link")
                or item.get("link")
                or item.get("dlink")
                or ""
            )
            thumbs: Dict[str, str] = {}
            thumb_single = item.get("thumbnail") or (item.get("thumbs") or {}).get("url3")
            if thumb_single:
                thumbs["original"] = thumb_single
            formatted = {
                "filename": filenamestr,
                "size": size_h,
                "size_bytes": size_b,
                "download_link": download,
                "is_directory": item.get("is_directory", False),
                "thumbnails": thumbs,
                "path": item.get("path", ""),
                "fs_id": item.get("fs_id", ""),
            }
            if item.get("direct_link"):
                formatted["direct_link"] = item["direct_link"]
            out.append(formatted)
        except Exception:
            continue
    return out
