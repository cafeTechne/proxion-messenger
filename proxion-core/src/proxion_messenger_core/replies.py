"""Reply threading module."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .messaging import Message


@dataclass
class ReplyContext:
    """Consolidated view of a reply and its target."""
    original_message_id: str
    original_content: str    # first 80 chars, plaintext after decrypt
    original_sender_webid: str
    reply: Message


def get_replies(
    messages: list[Message],
    target_message_id: str,
) -> list[Message]:
    """Return all messages that are direct replies to target_message_id.
    
    Pure function — works on already-fetched message list.
    """
    return [m for m in messages if m.reply_to_id == target_message_id and m.message_type == "text"]


def build_thread_view(
    messages: list[Message],
) -> list[tuple[Message, list[Message]]]:
    """Return a list of (root_message, [replies...]) pairs, sorted by root timestamp.
    
    Root messages are those with reply_to_id=None and message_type="text".
    Pure function.
    """
    roots = [m for m in messages if m.reply_to_id is None and m.message_type == "text"]
    roots.sort(key=lambda m: m.timestamp)
    
    view = []
    for root in roots:
        replies = get_replies(messages, root.message_id)
        replies.sort(key=lambda m: m.timestamp)
        view.append((root, replies))
    
    return view


def get_thread(messages: list, root_id: str, max_depth: int = 10) -> dict:
    """Build a nested reply tree starting from a root message.
    
    Recursively builds a tree structure where each message can have replies
    nested within. Supports both Message objects and plain dicts with 'id'
    and 'reply_to_id' fields.
    
    Parameters
    ----------
    messages : list
        List of Message objects or dicts with 'id' and 'reply_to_id'.
    root_id : str
        The ID of the root message to start the thread from.
    max_depth : int
        Maximum recursion depth to prevent infinite loops (default 10).
    
    Returns
    -------
    dict
        A nested tree: {"message": root_msg, "replies": [{"message": child, "replies": [...]}, ...]}
        Returns {"message": None, "replies": []} if root_id not found.
    """
    # Build a map of message_id -> message for fast lookup
    msg_map = {}
    for m in messages:
        # Support both Message objects and plain dicts
        if hasattr(m, "message_id"):
            msg_map[m.message_id] = m
        elif hasattr(m, "id"):
            msg_map[m.id] = m
        elif isinstance(m, dict) and "message_id" in m:
            msg_map[m["message_id"]] = m
        elif isinstance(m, dict) and "id" in m:
            msg_map[m["id"]] = m

    def _build(msg_id, depth):
        msg = msg_map.get(msg_id)
        if msg is None:
            return {"message": None, "replies": []}
        
        # If at max depth, still include message but no replies
        if depth >= max_depth:
            return {"message": msg, "replies": []}
        
        # Find all children (messages that reply to this one)
        children = []
        for m in messages:
            reply_to = None
            if hasattr(m, "reply_to_id"):
                reply_to = m.reply_to_id
            elif isinstance(m, dict) and "reply_to_id" in m:
                reply_to = m["reply_to_id"]
            
            if reply_to == msg_id:
                children.append(m)
        
        # Recursively build tree for each child
        child_trees = []
        for child in children:
            if hasattr(child, "message_id"):
                child_id = child.message_id
            elif hasattr(child, "id"):
                child_id = child.id
            elif isinstance(child, dict) and "message_id" in child:
                child_id = child["message_id"]
            else:
                child_id = child.get("id")
            
            child_trees.append(_build(child_id, depth + 1))
        
        return {"message": msg, "replies": child_trees}

    return _build(root_id, 0)


def flatten_thread(thread_tree: dict, depth: int = 0) -> list:
    """Convert a nested thread tree to a flat list of (depth, message) tuples.
    
    Traverses the tree in depth-first pre-order (parent before children).
    
    Parameters
    ----------
    thread_tree : dict
        A nested tree from get_thread.
    depth : int
        Current depth in the tree (incremented recursively).
    
    Returns
    -------
    list
        List of (depth, message) tuples in DFS pre-order.
    """
    result = []
    
    if thread_tree.get("message") is not None:
        result.append((depth, thread_tree["message"]))
    
    for reply in thread_tree.get("replies", []):
        result.extend(flatten_thread(reply, depth + 1))
    
    return result
