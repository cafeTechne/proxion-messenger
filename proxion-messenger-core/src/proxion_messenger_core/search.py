"""In-memory message search for filtered chat feeds."""

from __future__ import annotations

import re
import json
from dataclasses import dataclass
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .inbox import InboxEntry

@dataclass
class SearchResult:
    """A single search result match.
    
    Parameters
    ----------
    message_id : str
    thread_id : str
    from_webid : str
    timestamp : str
    snippet : str
        Highlighted text fragment showing the match.
    """
    message_id: str
    thread_id: str
    from_webid: str
    timestamp: str
    snippet: str

def search_messages(
    query: str,
    entries: List[InboxEntry],
    limit: int = 50,
    case_sensitive: bool = False
) -> List[SearchResult]:
    """Search for a query string across a list of messages.
    
    Parameters
    ----------
    query : str
        The search term.
    entries : List[InboxEntry]
        List of inbox entries to search.
    limit : int
        Maximum number of results to return.
    case_sensitive : bool
        If True, perform case-sensitive search.
    
    Returns
    -------
    List[SearchResult]
        Results sorted by timestamp (newest first).
    """
    if not query:
        return []

    results = []
    
    # Sort entries by timestamp descending
    sorted_entries = sorted(entries, key=lambda e: e.message.timestamp, reverse=True)
    
    for entry in sorted_entries:
        content = entry.message.content
        match_content = content if case_sensitive else content.lower()
        match_query = query if case_sensitive else query.lower()
        
        if match_query in match_content:
            # Create snippet
            match_idx = match_content.find(match_query)
            start = max(0, match_idx - 30)
            end = min(len(content), match_idx + len(query) + 30)
            snippet = content[start:end]
            if start > 0: snippet = "..." + snippet
            if end < len(content): snippet = snippet + "..."
            
            # Simple highlight
            flags = 0 if case_sensitive else re.IGNORECASE
            snippet = re.sub(f"({re.escape(query)})", r"==\1==", snippet, flags=flags)
            
            from datetime import datetime, timezone
            results.append(SearchResult(
                message_id=entry.message.message_id,
                thread_id=entry.thread_id if hasattr(entry, "thread_id") else "unknown",
                from_webid=entry.message.from_pub_hex,
                timestamp=datetime.fromtimestamp(entry.message.timestamp, tz=timezone.utc).isoformat(),
                snippet=snippet
            ))
            
            if len(results) >= limit:
                break
                
    return results


async def search_thread(
    stash,
    thread_id: str,
    query: str,
    limit: int = 50,
    case_sensitive: bool = False,
) -> list[SearchResult]:
    """Search for a query within a specific thread.
    
    Parameters
    ----------
    stash : StashClient
        Stash client for reading messages.
    thread_id : str
        ID of the thread to search.
    query : str
        Search query string (substring match).
    limit : int
        Maximum number of results to return.
    case_sensitive : bool
        If True, perform case-sensitive search.
    
    Returns
    -------
    list[SearchResult]
        Matching messages, sorted newest-first.
    """
    results = []
    
    try:
        # Try to load messages from stash for this thread
        key = f"threads/{thread_id}/messages.json"
        data = await stash.get(key)
        if not data:
            return results
        
        messages_dict = json.loads(data.decode())
        if isinstance(messages_dict, dict):
            messages = messages_dict.get("messages", [])
        else:
            messages = messages_dict if isinstance(messages_dict, list) else []
    except Exception:
        return results
    
    # Search messages
    for msg in messages:
        try:
            body = msg.get("body", msg.get("content", ""))
            match_body = body if case_sensitive else body.lower()
            match_query = query if case_sensitive else query.lower()
            
            if match_query in match_body:
                snippet = body[:200]
                flags = 0 if case_sensitive else re.IGNORECASE
                snippet = re.sub(f"({re.escape(query)})", r"==\1==", snippet, flags=flags)
                
                results.append(SearchResult(
                    message_id=msg.get("id", msg.get("message_id", "")),
                    thread_id=thread_id,
                    from_webid=msg.get("from_webid", msg.get("sender", "")),
                    timestamp=msg.get("timestamp", ""),
                    snippet=snippet,
                ))
                
                if len(results) >= limit:
                    break
        except Exception:
            continue
    
    return results


async def search_all_threads(
    stash,
    query: str,
    thread_ids: list[str],
    limit: int = 50,
) -> list[SearchResult]:
    """Search for a query across multiple threads.
    
    Parameters
    ----------
    stash : StashClient
        Stash client for reading.
    query : str
        Search query string.
    thread_ids : list[str]
        List of thread IDs to search.
    limit : int
        Maximum total results to return.
    
    Returns
    -------
    list[SearchResult]
        All matching messages from all threads, sorted newest-first.
    """
    all_results = []
    
    for thread_id in thread_ids:
        thread_results = await search_thread(stash, thread_id, query, limit=limit - len(all_results))
        all_results.extend(thread_results)
        
        if len(all_results) >= limit:
            break
    
    # Sort by timestamp, newest first
    all_results.sort(key=lambda r: r.timestamp, reverse=True)
    return all_results[:limit]

