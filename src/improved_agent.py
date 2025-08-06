"""
Improved Agent - Async-enabled, optimized chatbot backend
Features:
- Async/parallel processing for image generation
- Better error handling and recovery
- Improved observability and logging
- More maintainable code structure
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union
from uuid import uuid4

import pandas as pd
import sqlite3
from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel

from prompts import default_prompts
from config import (
    assistant_id,
    OPENAI_MODEL_CHAT,
    OPENAI_MODEL_STRUCT,
    MAX_SQL_RETRIES,
    HEAD_ROWS,
    CHAT_DOCS_DIR,
)
from structuredOutputs import (
    messageClassification,
    actionsRequired,
)

# ─────────────────────────── CONFIGURATION ─────────────────────────── #

load_dotenv()

# ─────────────────────────── ENHANCED LOGGER ─────────────────────────── #

class StructuredLogger:
    """Enhanced logger with structured logging capabilities"""
    
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)
        
    def info(self, message: str, **kwargs):
        extra = {"data": kwargs} if kwargs else {}
        self.logger.info(message, extra=extra)
        
    def warning(self, message: str, **kwargs):
        extra = {"data": kwargs} if kwargs else {}
        self.logger.warning(message, extra=extra)
        
    def error(self, message: str, **kwargs):
        extra = {"data": kwargs} if kwargs else {}
        self.logger.error(message, extra=extra)

logger = StructuredLogger("improved_agent")

# ─────────────────────────── DATA CLASSES ─────────────────────────── #

@dataclass
class ProcessingMetrics:
    """Track processing times and performance metrics"""
    start_time: float = field(default_factory=time.time)
    sql_time: Optional[float] = None
    image_time: Optional[float] = None
    total_time: Optional[float] = None
    sql_attempts: int = 0
    cache_hits: int = 0

@dataclass
class Artefacts:
    """Enhanced artefacts with metadata"""
    sql_query: Optional[str] = None
    data: Optional[pd.DataFrame] = None
    data_file: Optional[Path] = None
    image_file: Optional[Path] = None
    code_file: Optional[Path] = None
    answer: Optional[str] = None
    metrics: ProcessingMetrics = field(default_factory=ProcessingMetrics)
    session_id: str = field(default_factory=lambda: str(uuid4()))

# ─────────────────────────── ASYNC SERVICES ─────────────────────────── #

class AsyncLLM:
    """Async wrapper around OpenAI SDK with improved error handling"""

    def __init__(self, api_key: Optional[str] = None):
        self._client = AsyncOpenAI(api_key=api_key)

    async def chat(self, messages: List[dict], model: str = OPENAI_MODEL_CHAT) -> str:
        """Async chat completion with retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                resp = await self._client.chat.completions.create(
                    model=model, 
                    messages=messages,
                    timeout=30.0
                )
                return resp.choices[0].message.content.strip()
            except Exception as e:
                logger.warning(f"LLM chat attempt {attempt + 1} failed", error=str(e))
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)  # Exponential backoff

    async def struct(self, messages: List[dict], out_model: type[BaseModel]) -> BaseModel:
        """Async structured output with retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                resp = await self._client.beta.chat.completions.parse(
                    model=OPENAI_MODEL_STRUCT, 
                    messages=messages, 
                    response_format=out_model,
                    timeout=30.0
                )
                return resp.choices[0].message.parsed
            except Exception as e:
                logger.warning(f"LLM struct attempt {attempt + 1} failed", error=str(e))
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2 ** attempt)

class CacheManager:
    """Simple in-memory cache for common queries and responses"""
    
    def __init__(self, max_size: int = 100):
        self.cache: Dict[str, Dict] = {}
        self.max_size = max_size
        self.access_order: List[str] = []
    
    def get(self, key: str) -> Optional[Dict]:
        if key in self.cache:
            # Move to end (most recently used)
            self.access_order.remove(key)
            self.access_order.append(key)
            return self.cache[key]
        return None
    
    def set(self, key: str, value: Dict) -> None:
        if key in self.cache:
            self.access_order.remove(key)
        elif len(self.cache) >= self.max_size:
            # Remove least recently used
            oldest = self.access_order.pop(0)
            del self.cache[oldest]
        
        self.cache[key] = value
        self.access_order.append(key)

class AsyncFileManager:
    """Async file manager with better error handling"""

    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.counter: Dict[str, int] = {"data": 0, "image": 0, "code": 0}

    async def save_dataframe(self, df: pd.DataFrame) -> Path:
        """Save DataFrame asynchronously"""
        path = self.base_path / f"data_{self.counter['data']}.csv"
        
        # Run CPU-bound operation in thread pool
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, df.to_csv, str(path), False)
        
        self.counter["data"] += 1
        logger.info("Saved DataFrame", path=str(path), rows=len(df))
        return path

    async def save_image_bytes(self, data: bytes) -> Path:
        """Save image bytes asynchronously"""
        path = self.base_path / f"image_{self.counter['image']}.png"
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, path.write_bytes, data)
        
        self.counter["image"] += 1
        logger.info("Saved image", path=str(path), size=len(data))
        return path

    async def save_code(self, code: str) -> Path:
        """Save code asynchronously"""
        path = self.base_path / f"code_{self.counter['code']}.py"
        
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, path.write_text, code)
        
        self.counter["code"] += 1
        logger.info("Saved code", path=str(path), lines=len(code.split('\n')))
        return path

# ─────────────────────────── MAIN AGENT CLASS ─────────────────────────── #

class ImprovedAgentChat:
    """
    Enhanced agent with async capabilities and improved architecture
    """

    def __init__(self, conn: sqlite3.Connection, api_key: Optional[str] = None):
        self.llm = AsyncLLM(api_key)
        self.conn = conn
        self.assistant_id = assistant_id
        self.prompts = default_prompts
        self.history: List[dict] = []
        self.context: str = "There is no relevant context for this conversation."
        self.artefacts = Artefacts()
        self.base_path: Path = self._init_chat_dir()
        self.fs = AsyncFileManager(self.base_path)
        self.cache = CacheManager()

    async def execute(self, user_message: str) -> Tuple[str, Dict]:
        """
        Enhanced execution with metrics and async processing
        Returns: (response, metrics_dict)
        """
        start_time = time.time()
        session_id = self.artefacts.session_id
        
        logger.info("Processing request", session_id=session_id, message_length=len(user_message))

        try:
            # Check cache first
            cache_key = f"{user_message}:{self.context[:100]}"
            cached = self.cache.get(cache_key)
            if cached:
                logger.info("Cache hit", session_id=session_id)
                return cached["response"], {"cached": True, "time": time.time() - start_time}

            # 1) Translation and preprocessing (parallel where possible)
            user_en_task = self._translate(user_message, src="spanish", tgt="english")
            user_en = await user_en_task
            
            logger.info("Translated message", session_id=session_id, original_length=len(user_message))

            # 2) Request normalization and classification
            request = await self._to_request(user_en)
            classification = await self._classify(request)
            
            logger.info("Classification complete", 
                       session_id=session_id,
                       on_topic=classification.is_on_topic,
                       context_sufficient=classification.is_context_sufficient)

            # 3) Route based on classification
            if not (classification.is_on_topic and classification.is_context_sufficient):
                reason = "not_on_topic" if not classification.is_on_topic else "context_not_sufficient"
                reply_en = await self._bad_flow(request, reason)
                response_es = await self._translate(reply_en, src="english", tgt="spanish")
                
                metrics = {
                    "session_id": session_id,
                    "total_time": time.time() - start_time,
                    "flow": "bad",
                    "reason": reason
                }
                return response_es, metrics

            # 4) Good flow with async optimization
            reply_en, interaction_summary = await self._good_flow_async(request)

            # 5) Update state
            self.history.append({"role": "assistant", "content": reply_en})
            await self._update_context(interaction_summary)

            # 6) Final translation
            response_es = await self._translate(reply_en, src="english", tgt="spanish")

            # Cache successful responses
            self.cache.set(cache_key, {"response": response_es, "metrics": self.artefacts.metrics})

            metrics = {
                "session_id": session_id,
                "total_time": time.time() - start_time,
                "sql_time": self.artefacts.metrics.sql_time,
                "image_time": self.artefacts.metrics.image_time,
                "sql_attempts": self.artefacts.metrics.sql_attempts,
                "flow": "good"
            }

            logger.info("Request completed", session_id=session_id, total_time=metrics["total_time"])
            return response_es, metrics

        except Exception as e:
            logger.error("Request failed", session_id=session_id, error=str(e))
            error_response = "Lo siento, ha ocurrido un error procesando tu solicitud. Por favor, inténtalo de nuevo."
            return error_response, {"error": str(e), "session_id": session_id}

    async def _good_flow_async(self, request: str) -> Tuple[str, str]:
        """Optimized good flow with parallel processing"""
        self.history.append({"role": "user", "content": request})

        # Get required actions
        actions = await self._get_actions(request)
        logger.info("Actions determined", 
                   new_sql=actions.is_new_sql_query_needed,
                   new_image=actions.is_new_image_needed)

        # Branch logic with async optimization
        if not actions.is_new_sql_query_needed and not actions.is_new_image_needed:
            return await self._answer_only_branch(request)

        if actions.is_new_image_needed and not actions.is_new_sql_query_needed:
            return await self._image_only_branch_async(request)

        return await self._data_refresh_branch_async(request, also_image=actions.is_new_image_needed)

    async def _data_refresh_branch_async(self, request: str, *, also_image: bool) -> Tuple[str, str]:
        """Optimized data refresh with parallel image generation"""
        logger.info("Data refresh branch", also_image=also_image)

        # Start SQL processing
        sql_start = time.time()
        sql_result = await self._supervised_sql_async(request)
        sql_query, df, data_path, ok = sql_result
        self.artefacts.metrics.sql_time = time.time() - sql_start

        if not ok:
            return "Sorry, I couldn't retrieve the requested data. Please try again.", "Data retrieval failed."

        # Store SQL results
        art_dict = {
            "sql_query": sql_query,
            "data": df,
            "data_file": data_path,
        }

        # Start parallel tasks: final answer generation and image creation
        tasks = []
        
        # Always generate the final answer
        answer_task = self._create_final_answer(request, df)
        tasks.append(answer_task)
        
        # If image is needed, start it in parallel
        if also_image:
            image_start = time.time()
            image_task = self._run_python_image_async(
                request,
                df.head(HEAD_ROWS).to_string(index=False),
                data_path,
            )
            tasks.append(image_task)

        # Wait for all tasks to complete
        if also_image:
            final_answer, (img_bytes, code, img_path, code_path) = await asyncio.gather(*tasks)
            self.artefacts.metrics.image_time = time.time() - image_start
            
            art_dict.update({
                "image_file": img_path,
                "code_file": code_path,
                "image": img_bytes,
                "code": code,
            })
        else:
            final_answer = await answer_task

        summary = await self._record_artefacts_async(art_dict, request, final_answer)
        return final_answer, summary

    async def _supervised_sql_async(self, request: str) -> Tuple[str, pd.DataFrame, Optional[Path], bool]:
        """Async SQL execution with improved error handling"""
        base_query = ""
        
        for attempt in range(1, MAX_SQL_RETRIES + 1):
            self.artefacts.metrics.sql_attempts = attempt
            logger.info("SQL attempt", attempt=attempt, max_retries=MAX_SQL_RETRIES)
            
            try:
                sql_query, df = await self._single_sql_round_async(request, base_query)
                
                if not df.empty:
                    data_path = await self.fs.save_dataframe(df)
                    return sql_query, df, data_path, True
                
                base_query = sql_query  # Provide feedback for next attempt
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.warning("SQL attempt failed", attempt=attempt, error=str(e))
                if attempt == MAX_SQL_RETRIES:
                    break
                await asyncio.sleep(2 ** attempt)

        logger.error("SQL failed after all attempts", max_retries=MAX_SQL_RETRIES)
        return "", pd.DataFrame(), None, False

    async def _single_sql_round_async(self, request: str, previous_query: str) -> Tuple[str, pd.DataFrame]:
        """Single SQL round with async LLM calls"""
        # Generate simple question
        msgs = self.prompts["message_to_simple_question"].copy()
        msgs.append({
            "role": "user",
            "content": f"Transform the following request into a simple question that can be answered using SQL: {request}"
        })
        simple_q = await self.llm.chat(msgs)
        logger.info("Generated simple question", question=simple_q)

        # Generate SQL query
        msgs = self.prompts["sql_query"].copy()
        content = simple_q if not previous_query else f"{simple_q}\nConsider that the previous query failed: {previous_query}"
        msgs.append({"role": "user", "content": content})
        sql_query = await self.llm.chat(msgs)
        logger.info("Generated SQL", query=sql_query)

        # Execute SQL in thread pool (since pandas.read_sql_query is blocking)
        try:
            loop = asyncio.get_event_loop()
            df = await loop.run_in_executor(None, pd.read_sql_query, sql_query, self.conn)
            logger.info("SQL executed successfully", rows=len(df))
        except Exception as e:
            logger.warning("SQL execution failed", error=str(e))
            df = pd.DataFrame()

        return sql_query, df

    async def _run_python_image_async(self, request: str, data_sample: str, data_filename: Path) -> Tuple[bytes, str, Path, Path]:
        """Async image generation with better error handling"""
        try:
            # These operations are inherently async/IO-bound
            file_id = await self._upload_file_openai_async(data_filename)
            logger.info("File uploaded to OpenAI", file_id=file_id)

            instructions = await self._request_to_image_instr(request, data_sample)
            logger.info("Image instructions generated")

            messages = await self._create_image_async(instructions, file_id)
            img_bytes, code, img_path, code_path = await self._extract_code_and_image_async(messages)
            
            logger.info("Image generation completed", 
                       image_size=len(img_bytes), 
                       code_lines=len(code.split('\n')))
            
            return img_bytes, code, img_path, code_path
            
        except Exception as e:
            logger.error("Image generation failed", error=str(e))
            raise RuntimeError(f"Image generation failed: {str(e)}")

    # Additional async helper methods would go here...
    # (I'll include key ones for the example)

    async def _translate(self, text: str, *, src: str, tgt: str) -> str:
        """Async translation"""
        msgs = self.prompts["translation"].copy()
        msgs.append({
            "role": "user",
            "content": f"Translate the following text from {src} to {tgt}:\n{text}",
        })
        return await self.llm.chat(msgs)

    async def _classify(self, request: str) -> messageClassification:
        """Async classification"""
        msgs = self.prompts["classification"].copy()
        msgs.append({"role": "user", "content": request})
        return await self.llm.struct(msgs, messageClassification)

    async def _get_actions(self, request: str) -> actionsRequired:
        """Async action determination"""
        msgs = self.prompts["actions"].copy()
        msgs.append({"role": "user", "content": request})
        return await self.llm.struct(msgs, actionsRequired)

    async def _create_final_answer(self, request: str, df: pd.DataFrame, prev_answer: str = None) -> str:
        """Async final answer generation"""
        msgs = self.prompts["final_answer"].copy()
        user_msg = f"The user request is: {request}\nThe data from the query is:\n{df.to_string(index=False)}.\n"
        
        if prev_answer:
            user_msg += f"The previous answer was: {prev_answer}\n"
        user_msg += "Create a final answer for the user based on the request and the data."
        
        msgs.append({"role": "user", "content": user_msg})
        return await self.llm.chat(msgs)

    # ... (Additional async methods would be implemented similarly)

    def _init_chat_dir(self) -> Path:
        """Initialize chat directory (synchronous)"""
        CHAT_DOCS_DIR.mkdir(exist_ok=True)
        run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
        path = CHAT_DOCS_DIR / run_id
        path.mkdir()
        logger.info("Chat directory created", path=str(path))
        return path

    # Additional helper methods for completeness...
    async def _to_request(self, user_en: str) -> str:
        msgs = self.prompts["message_to_request"].copy()
        msgs.append({
            "role": "user",
            "content": (
                f"The conversation context is: {self.context}\n"
                f"The user message is: {user_en}.\n"
                "Explain what the user wants in a clear, straightforward way."
            ),
        })
        return await self.llm.chat(msgs)

    async def _bad_flow(self, request: str, reason_key: str) -> str:
        msgs = self.prompts[reason_key].copy()
        msgs.append({"role": "user", "content": request})
        return await self.llm.chat(msgs)

    async def _answer_only_branch(self, request: str) -> Tuple[str, str]:
        logger.info("Answer only branch - reusing artefacts")
        final_answer = await self._create_final_answer(request, self.artefacts.data, self.artefacts.answer)
        summary = await self._record_artefacts_async({"answer": final_answer}, request, final_answer)
        return final_answer, summary

    async def _image_only_branch_async(self, request: str) -> Tuple[str, str]:
        logger.info("Image only branch - reusing data")
        
        img_bytes, code, img_path, code_path = await self._run_python_image_async(
            request,
            self.artefacts.data.head(HEAD_ROWS).to_string(index=False),
            self.artefacts.data_file,
        )

        art_dict = {
            "image_file": img_path,
            "code_file": code_path,
            "image": img_bytes,
            "code": code,
        }
        final_answer = "The image has been updated successfully."
        summary = await self._record_artefacts_async(art_dict, request, final_answer)
        return final_answer, summary

    async def _record_artefacts_async(self, new_items: Dict[str, object], request: str, answer: str) -> str:
        # Merge new items into dataclass
        for k, v in new_items.items():
            setattr(self.artefacts, k, v)

        summary = await self._summarise_interaction(request, answer, new_items.keys())
        logger.info("Interaction summarized")
        return summary

    async def _summarise_interaction(self, user_msg: str, assistant_msg: str, artefact_keys) -> str:
        art = ", ".join(k for k in artefact_keys if k != "answer") or "none"
        msgs = self.prompts["summarize_interaction"].copy()
        msgs.append({
            "role": "user",
            "content": (
                f"The user message is: {user_msg}\n"
                f"The assistant message is: {assistant_msg}.\n"
                f"Artefacts created: {art}. Summarise the interaction."
            ),
        })
        return await self.llm.chat(msgs)

    async def _update_context(self, new_summary: str) -> None:
        msgs = self.prompts["update_context"].copy()
        msgs.append({
            "role": "user",
            "content": (
                f"Previous context: {self.context}\n"
                f"New interaction summary: {new_summary}.\n"
                "Update the context accordingly."
            ),
        })
        logger.info("Updating context", previous_context=self.context[:100])
        self.context = await self.llm.chat(msgs)
        logger.info("Context updated", new_context=self.context[:100])

    # Placeholder async methods for image processing
    async def _upload_file_openai_async(self, csv_path: Path) -> str:
        # This would be implemented with proper async file operations
        # For now, using thread pool
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._upload_file_sync, csv_path)
    
    def _upload_file_sync(self, csv_path: Path) -> str:
        # Sync version for thread pool execution
        with open(csv_path, "rb") as f:
            file_obj = self.llm._client.files.create(file=f, purpose="assistants")
        return file_obj.id

    async def _request_to_image_instr(self, request: str, sample: str) -> str:
        msgs = self.prompts["message_to_image_instruction"].copy()
        msgs.append({
            "role": "user",
            "content": (
                f"Transform the following request into image creation instructions: "
                f"{request}\nThis is a sample of the data:\n{sample}"
            ),
        })
        return await self.llm.chat(msgs)

    async def _create_image_async(self, instructions: str, file_id: str):
        # This would need proper async implementation with OpenAI SDK
        # For now, using thread pool for the blocking operations
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._create_image_sync, instructions, file_id)
    
    def _create_image_sync(self, instructions: str, file_id: str):
        # Sync version for thread pool execution
        thread = self.llm._client.beta.threads.create(
            messages=[{
                "role": "user",
                "content": (
                    "Write python code to create an intuitive chart with the data "
                    "and export the image as a png.\n"
                    f"Follow these instructions: {instructions}"
                ),
                "attachments": [{"file_id": file_id, "tools": [{"type": "code_interpreter"}]}],
            }]
        )
        run = self.llm._client.beta.threads.runs.create_and_poll(
            thread_id=thread.id,
            assistant_id=self.assistant_id,
            instructions=instructions,
        )
        return self._wait_for_run_sync(run, thread.id)
    
    def _wait_for_run_sync(self, run, thread_id: str):
        while True:
            status = run.status
            if status == "completed":
                return self.llm._client.beta.threads.messages.list(thread_id=thread_id).data
            if status in {"failed", "cancelled"}:
                raise RuntimeError(f"Image generation failed: {status}")
            time.sleep(5)

    async def _extract_code_and_image_async(self, messages):
        # This would be implemented with proper async operations
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._extract_code_and_image_sync, messages)
    
    def _extract_code_and_image_sync(self, messages):
        image_msg = messages[0]
        code_msg = messages[1]

        # Download image bytes
        img_file_id = image_msg.attachments[0].file_id
        img_bytes = self.llm._client.files.content(img_file_id).read()
        
        # Save files synchronously (will be wrapped in executor)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        img_path = loop.run_until_complete(self.fs.save_image_bytes(img_bytes))

        # Extract and save code
        raw_code = code_msg.content[0].text.value
        code = loop.run_until_complete(self._filter_code_async(raw_code))
        code_path = loop.run_until_complete(self.fs.save_code(code))
        
        loop.close()
        return img_bytes, code, img_path, code_path

    async def _filter_code_async(self, text: str) -> str:
        msgs = self.prompts["message_to_code_extraction"].copy()
        msgs.append({"role": "user", "content": f"Extract the code from:\n{text}"})
        return await self.llm.chat(msgs)
