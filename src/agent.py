"""
Refactored – readability-oriented – agent class.
Focuses on clear separation of concerns, structured logging and safer I/O.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import sqlite3
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

from prompts import default_prompts
from config import (
    assistant_id,
    OPENAI_MODEL_CHAT,
    OPENAI_MODEL_STRUCT,
    MAX_SQL_RETRIES,
    HEAD_ROWS,
    CHAT_DOCS_DIR,
    client,   
    )
from structuredOutputs import (
    messageClassification,
    actionsRequired,
)

# ───────────────────────────── CONFIG ───────────────────────────── #

load_dotenv()

# ───────────────────────────── LOGGER ───────────────────────────── #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
)
logger = logging.getLogger("agent")


# ───────────────────────────── DATA CLASSES ─────────────────────── #

@dataclass
class Artefacts:
    sql_query: Optional[str] = None
    data: Optional[pd.DataFrame] = None
    data_file: Optional[Path] = None
    image_file: Optional[Path] = None
    code_file: Optional[Path] = None
    answer: Optional[str] = None


# ────────────────────────── HELPER SERVICES ─────────────────────── #

class LLM:
    """Thin wrapper around OpenAI SDK to simplify mocking / tracing."""

    def __init__(self, client: OpenAI = client):
        self._client = client

    # --- generic helpers -------------------------------------------------- #

    def chat(self, messages: List[dict], model: str = OPENAI_MODEL_CHAT) -> str:
        resp = self._client.chat.completions.create(model=model, messages=messages)
        return resp.choices[0].message.content.strip()

    def struct(
        self, messages: List[dict], out_model: type[BaseModel]
    ) -> BaseModel:
        resp = self._client.beta.chat.completions.parse(
            model=OPENAI_MODEL_STRUCT, messages=messages, response_format=out_model
        )
        return resp.choices[0].message.parsed


class FileManager:
    """All filesystem operations live here."""

    def __init__(self, base_path: Path):
        self.base_path = base_path
        self.counter: Dict[str, int] = {
            "data": 0,
            "image": 0,
            "code": 0,
        }

    def save_dataframe(self, df: pd.DataFrame) -> Path:
        path = self.base_path / f"data_{self.counter['data']}.csv"
        df.to_csv(path, index=False)
        self.counter["data"] += 1
        logger.info("Saved DataFrame to %s", path)
        return path

    def save_image_bytes(self, data: bytes) -> Path:
        path = self.base_path / f"image_{self.counter['image']}.png"
        path.write_bytes(data)
        self.counter["image"] += 1
        logger.info("Saved image to %s", path)
        return path

    def save_code(self, code: str) -> Path:
        path = self.base_path / f"code_{self.counter['code']}.py"
        path.write_text(code)
        self.counter["code"] += 1
        logger.info("Saved python code to %s", path)
        return path


# ───────────────────────────── AGENT CLASS ──────────────────────── #

class AgentChat:
    """Main façade orchestrating a single user session."""

    # ---------- construction ---------- #

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.llm = LLM()
        self.conn = conn
        self.assistant_id = assistant_id
        self.prompts = default_prompts
        self.history: List[dict] = []
        self.context: str = "There is no relevant context for this conversation."
        self.artefacts = Artefacts()
        self.base_path: Path = self._init_chat_dir()
        self.fs = FileManager(self.base_path)

    # ---------- public entry-point ---------- #

    def execute(self, user_message: str) -> str:
        """
        High-level workflow called by Streamlit UI.
        Handles both 'good' and 'bad' flows and returns ES response.
        """
        logger.info("New user message received")
        logger.info("User message: %s", user_message)

        # 1) Translate incoming Spanish → English
        user_en = self._translate(user_message, src="spanish", tgt="english")
        logger.info("Translated user message: %s", user_en)

        # 2) Normalise into a clean request
        request = self._to_request(user_en)
        logger.info("Normalised request: %s", request)

        # 3) Classify request
        classification: messageClassification = self._classify(request)
        logger.info(
            "Classification: on_topic=%s, context_sufficient=%s",
            classification.is_on_topic,
            classification.is_context_sufficient,
        )

        # 4.1) Bad flow
        if not (classification.is_on_topic and classification.is_context_sufficient):
            reason = (
                "not_on_topic"
                if not classification.is_on_topic
                else "context_not_sufficient"
            )
            logger.info("Bad flow: %s", reason)
            
            reply_en = self._bad_flow(request, reason)
            return self._translate(reply_en, src="english", tgt="spanish")

        # 4.2) Good flow
        reply_en, interaction_summary = self._good_flow(request)

        # Update conversation state
        self.history.append({"role": "assistant", "content": reply_en})
        self._update_context(interaction_summary)

        # Final translation back to ES
        return self._translate(reply_en, src="english", tgt="spanish")

    # ---------- GOOD FLOW ---------- #

    def _good_flow(self, request: str) -> Tuple[str, str]:
        """Dispatches to the right branch according to LLM-derived actions."""
        self.history.append({"role": "user", "content": request})

        actions: actionsRequired = self._get_actions(request)
        logger.info(
            "Actions required: new_sql_query=%s, new_image=%s",
            actions.is_new_sql_query_needed,
            actions.is_new_image_needed,
        )

        if not actions.is_new_sql_query_needed and not actions.is_new_image_needed:
            return self._answer_only_branch(request)

        if actions.is_new_image_needed and not actions.is_new_sql_query_needed:
            return self._image_only_branch(request)

        return self._data_refresh_branch(request, also_image=actions.is_new_image_needed)

    # -- Branch helpers --

    def _answer_only_branch(self, request: str) -> Tuple[str, str]:
        logger.info("Branch A – answer only (reuse artefacts)")
        final_answer = self._create_final_answer(
            request, self.artefacts.data, self.artefacts.answer
        )
        summary = self._record_artefacts({"answer": final_answer}, request, final_answer)
        return final_answer, summary

    def _image_only_branch(self, request: str) -> Tuple[str, str]:
        logger.info("Branch B – image only (reuse data)")

        img_bytes, code, img_path, code_path = self._run_python_image(
            request,
            self.artefacts.data.head(HEAD_ROWS).to_string(index=False),
            self.artefacts.data_file,
        )

        art = {
            "image_file": img_path,
            "code_file": code_path,
            "image": img_bytes,
            "code": code,
        }
        final_answer = "The image has been updated successfully."
        summary = self._record_artefacts(art, request, final_answer)
        return final_answer, summary

    def _data_refresh_branch(
        self, request: str, *, also_image: bool
    ) -> Tuple[str, str]:
        logger.info("Branch C – fresh SQL (image: %s)", also_image)

        sql_query, df, data_path, ok = self._supervised_sql(request)
        if not ok:
            msg = "Sorry, I couldn't retrieve the requested data. Please try again."
            return msg, "Data retrieval failed."

        art_dict = {
            "sql_query": sql_query,
            "data": df,
            "data_file": data_path,
        }

        if also_image:
            img_bytes, code, img_path, code_path = self._run_python_image(
                request,
                df.head(HEAD_ROWS).to_string(index=False),
                data_path,
            )
            art_dict.update(
                {
                    "image_file": img_path,
                    "code_file": code_path,
                    "image": img_bytes,
                    "code": code,
                }
            )

        final_answer = self._create_final_answer(request, df)
        summary = self._record_artefacts(art_dict, request, final_answer)
        return final_answer, summary

    # ---------- core LLM helpers ---------- #

    def _translate(self, text: str, *, src: str, tgt: str) -> str:
        msgs = self.prompts["translation"].copy()
        msgs.append(
            {
                "role": "user",
                "content": f"Translate the following text from {src} to {tgt}:\n{text}",
            }
        )
        return self.llm.chat(msgs)

    def _to_request(self, user_en: str) -> str:
        msgs = self.prompts["message_to_request"].copy()
        msgs.append(
            {
                "role": "user",
                "content": (
                    f"The conversation context is: {self.context}\n"
                    f"The user message is: {user_en}.\n"
                    "Explain what the user wants in a clear, straightforward way."
                ),
            }
        )
        return self.llm.chat(msgs)

    def _classify(self, request: str) -> messageClassification:
        msgs = self.prompts["classification"].copy()
        msgs.append({"role": "user", "content": request})
        return self.llm.struct(msgs, messageClassification)

    def _get_actions(self, request: str) -> actionsRequired:
        msgs = self.prompts["actions"].copy()
        msgs.append({"role": "user", "content": request})
        return self.llm.struct(msgs, actionsRequired)

    def _bad_flow(self, request: str, reason_key: str) -> str:
        msgs = self.prompts[reason_key].copy()
        msgs.append({"role": "user", "content": request})
        return self.llm.chat(msgs)

    def _create_final_answer(
        self, request: str, df: pd.DataFrame, prev_answer: str | None = None
    ) -> str:
        msgs = self.prompts["final_answer"].copy()
        user_msg = (
            f"The user request is: {request}\n"
            f"The data from the query is:\n{df.to_string(index=False)}.\n"
        )
        logger.debug("messages: %s", user_msg)
        if prev_answer:
            user_msg += f"The previous answer was: {prev_answer}\n"
        user_msg += (
            "Create a final answer for the user based on the request and the data."
        )
        msgs.append({"role": "user", "content": user_msg})
        return self.llm.chat(msgs)

    # ---------- context / history ---------- #

    def _record_artefacts(
        self, new_items: Dict[str, object], request: str, answer: str
    ) -> str:
        # merge new items into dataclass
        for k, v in new_items.items():
            setattr(self.artefacts, k, v)

        summary = self._summarise_interaction(request, answer, new_items.keys())
        logger.info("Interaction summarised")
        return summary

    def _summarise_interaction(
        self, user_msg: str, assistant_msg: str, artefact_keys
    ) -> str:
        art = ", ".join(k for k in artefact_keys if k != "answer") or "none"
        msgs = self.prompts["summarize_interaction"].copy()
        msgs.append(
            {
                "role": "user",
                "content": (
                    f"The user message is: {user_msg}\n"
                    f"The assistant message is: {assistant_msg}.\n"
                    f"Artefacts created: {art}. Summarise the interaction."
                ),
            }
        )
        return self.llm.chat(msgs)

    def _update_context(self, new_summary: str) -> None:
        msgs = self.prompts["update_context"].copy()
        msgs.append(
            {
                "role": "user",
                "content": (
                    f"Previous context: {self.context}\n"
                    f"New interaction summary: {new_summary}.\n"
                    "Update the context accordingly."
                ),
            }
        )
        logger.info("Previous Context: %s", self.context)
        self.context = self.llm.chat(msgs)
        logger.info("New Context: %s", self.context)
        logger.info("Context updated.")

    # ---------- SQL path ---------- #

    def _supervised_sql(
        self, request: str
    ) -> Tuple[str, pd.DataFrame, Optional[Path], bool]:
        base_query = ""
        for attempt in range(1, MAX_SQL_RETRIES + 1):
            logger.info("SQL attempt %d/%d", attempt, MAX_SQL_RETRIES)
            sql_query, df = self._single_sql_round(request, base_query)

            if not df.empty:
                data_path = self.fs.save_dataframe(df)
                return sql_query, df, data_path, True

            base_query = sql_query  # give feedback to LLM
            time.sleep(1)

        logger.error("SQL failed after %d attempts", MAX_SQL_RETRIES)
        return "", pd.DataFrame(), None, False

    def _single_sql_round(self, request: str, previous_query: str):
        # 1) question →
        msgs = self.prompts["message_to_simple_question"].copy()
        msgs.append(
            {
                "role": "user",
                "content": (
                    "Transform the following request into a simple question that "
                    f"can be answered using SQL: {request}"
                ),
            }
        )
        simple_q = self.llm.chat(msgs)
        logger.info("Simple question: %s", simple_q)

        # 2) simple q → SQL
        msgs = self.prompts["sql_query"].copy()
        content = (
            simple_q
            if not previous_query
            else f"{simple_q}\nConsider that the previous query failed: {previous_query}"
        )
        msgs.append({"role": "user", "content": content})
        sql_query = self.llm.chat(msgs)
        logger.info("SQL query: %s", sql_query)

        # 3) run SQL
        try:
            df = pd.read_sql_query(sql_query, self.conn)
        except Exception as exc:  # noqa: BLE001
            logger.warning("SQL execution error: %s", exc)
            df = pd.DataFrame()

        return sql_query, df

    # ---------- Image path ---------- #

    # (identical to original logic but reorganised for clarity)
    def _run_python_image(
        self, request: str, data_sample: str, data_filename: Path
    ) -> Tuple[bytes, str, Path, Path]:
        # upload CSV
        file_id = self._upload_file_openai(data_filename)
        logger.info("Uploaded file to OpenAI (%s)", file_id)

        # LLM: turn request into plotting instructions
        instr = self._request_to_image_instr(request, data_sample)
        logger.info("Image instructions: %s", instr)

        # ask assistant to run code / create image
        msgs = self._create_image(instr, file_id)
        img_bytes, code, img_path, code_path = self._extract_code_and_image(msgs)
        return img_bytes, code, img_path, code_path

    def _upload_file_openai(self, csv_path: Path) -> str:
        file_obj = self.llm._client.files.create(
            file=open(csv_path, "rb"), purpose="assistants"
        )
        return file_obj.id

    def _request_to_image_instr(self, request: str, sample: str) -> str:
        msgs = self.prompts["message_to_image_instruction"].copy()
        msgs.append(
            {
                "role": "user",
                "content": (
                    f"Transform the following request into image creation instructions: "
                    f"{request}\nThis is a sample of the data:\n{sample}"
                ),
            }
        )
        return self.llm.chat(msgs)

    def _create_image(self, instructions: str, file_id: str):
        thread = self.llm._client.beta.threads.create(
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Write python code to create an intuitive chart with the data "
                        "and export the image as a png.\n"
                        f"Follow these instructions: {instructions}"
                    ),
                    "attachments": [{"file_id": file_id, "tools": [{"type": "code_interpreter"}]}],
                }
            ]
        )
        run = self.llm._client.beta.threads.runs.create_and_poll(
            thread_id=thread.id,
            assistant_id=self.assistant_id,
            instructions=instructions,
        )
        return self._wait_for_run(run, thread.id)

    def _wait_for_run(self, run, thread_id: str):
        while True:
            status = run.status
            logger.info("Run status: %s", status)
            if status == "completed":
                return self.llm._client.beta.threads.messages.list(thread_id=thread_id).data
            if status in {"failed", "cancelled"}:
                raise RuntimeError(f"Image generation failed: {status}")
            else:
                logger.info("Waiting for image generation to complete...")
                # wait for 5 seconds before checking again
            time.sleep(10)

    def _extract_code_and_image(self, messages: List[dict]):
        image_msg = messages[0]
        code_msg = messages[1]

        # download image bytes
        img_file_id = image_msg.attachments[0].file_id
        img_bytes = self.llm._client.files.content(img_file_id).read()
        img_path = self.fs.save_image_bytes(img_bytes)

        # isolate python code
        raw_code = code_msg.content[0].text.value
        code = self._filter_code(raw_code)
        code_path = self.fs.save_code(code)

        return img_bytes, code, img_path, code_path

    def _filter_code(self, text: str) -> str:
        msgs = self.prompts["message_to_code_extraction"].copy()
        msgs.append({"role": "user", "content": f"Extract the code from:\n{text}"})
        return self.llm.chat(msgs)

    # ---------- private helpers ---------- #

    def _init_chat_dir(self) -> Path:
        CHAT_DOCS_DIR.mkdir(exist_ok=True)
        run_id = datetime.now().strftime("%Y%m%dT%H%M%S")
        path = CHAT_DOCS_DIR / run_id
        path.mkdir()
        logger.info("Chat directory created at %s", path)
        return path
