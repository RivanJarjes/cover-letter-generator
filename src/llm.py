from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from openai import OpenAI
import pypdf

logger = logging.getLogger(__name__)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-5")
_FILENAME_MODEL = os.getenv("OPENAI_FILENAME_MODEL", "gpt-5")
_MAX_TOKENS = int(os.getenv("OPENAI_MAX_TOKENS", "1200"))
_FILENAME_MAX_TOKENS = int(os.getenv("OPENAI_FILENAME_MAX_TOKENS", "60"))
_TEMPERATURE = float(os.getenv("OPENAI_TEMPERATURE", "0.3"))
_TOP_P = float(os.getenv("OPENAI_TOP_P", "0.95"))

# Default prompts
_DEFAULT_COVER_LETTER_PROMPT = (
    "You write concise, tailored cover letters.\n"
    "**Avoid em dashes (—);** use commas or periods instead.\n"
    "Do not mention experience that is absent from the resume.\n"
    "Keep the final letter be one printed page (roughly 4 short paragraphs).\n"
    "**Stay professional, specific, and eliminate filler.**\n"
    "Make sure to have a proper ending and signature area.\n"
    "**ONLY IF INFO IS SUPPLIED BY THE RESUME**, have a header at the top consisting of the user's name, city, email, "
    "and/or phone number, **only if given by resume.**\n"
    "**DO NOT put any placeholders in the cover letter**, leverage whatever is given only."
)
_DEFAULT_FILENAME_PROMPT = (
    "You generate short, filesystem-safe PDF filenames for cover letters. "
    "Output ONLY the filename without extension. Use snake_case. "
    "Format: company_role (e.g., google_software_engineer, meta_product_manager). "
    "Keep it under 40 characters. No spaces, no special characters except underscores."
)

_COVER_LETTER_PROMPT = _DEFAULT_COVER_LETTER_PROMPT
_FILENAME_PROMPT = _DEFAULT_FILENAME_PROMPT

# Models that support temperature and top_p
_MODELS_WITH_SAMPLING = {"gpt-5.1"}

_CLIENT: Optional[OpenAI] = None


def _build_prompt(resume_text: str, job_description: str, sample_text: Optional[str]) -> str:
	sample_section = ""
	if sample_text:
		sample_section = (
			"<cover_letter_sample>\n"
			f"{sample_text.strip()}\n"
			"</cover_letter_sample>\n\n"
			"Use the cover letter sample only as a stylistic reference; do not copy it.\n\n"
		)

	return (
		_COVER_LETTER_PROMPT + "\n\n"
		"<resume>\n"
		f"{resume_text.strip()}\n"
		"</resume>\n\n"
		+ sample_section
		+ "<job_description>\n"
		+ f"{job_description.strip()}\n"
		+ "</job_description>\n\n"
		+ "Draft the complete cover letter now."
	)


def _get_client() -> OpenAI:
	global _CLIENT
	if _CLIENT is None:
		api_key = os.getenv("OPENAI_API_KEY")
		if not api_key:
			error_msg = "OPENAI_API_KEY environment variable is required"
			logger.error(error_msg)
			raise RuntimeError(error_msg)
		try:
			_CLIENT = OpenAI(api_key=api_key)
			logger.info("OpenAI client initialized successfully")
		except Exception as e:
			logger.error(f"Failed to initialize OpenAI client: {e}", exc_info=True)
			raise
	return _CLIENT


def _extract_text_from_file(file_path: str) -> str:
	try:
		path = Path(file_path)
		if path.suffix.lower() == ".pdf":
			reader = pypdf.PdfReader(str(path))
			text_parts = []
			for page in reader.pages:
				text_parts.append(page.extract_text() or "")
			logger.info(f"Extracted text from PDF: {file_path}")
			return "\n".join(text_parts)
		else:
			text = path.read_text(encoding="utf-8", errors="ignore")
			logger.info(f"Extracted text from file: {file_path}")
			return text
	except Exception as e:
		logger.error(f"Failed to extract text from file {file_path}: {e}", exc_info=True)
		raise


def generate_cover_letter(
	resume_path: str,
	job_description: str,
	sample_path: Optional[str] = None,
) -> str:
	try:
		resume_text = _extract_text_from_file(resume_path)
		logger.debug(f"Resume text sample: {resume_text[:2000]}")
		sample_text = None
		if sample_path:
			sample_text = _extract_text_from_file(sample_path)
			logger.info("Sample cover letter loaded")

		prompt = _build_prompt(resume_text, job_description, sample_text)
		client = _get_client()

		# Build request params - only include temperature/top_p for supported models
		request_params = {
			"model": _MODEL_NAME,
			"max_output_tokens": _MAX_TOKENS,
			"input": [
				{
					"role": "system",
					"content": "You craft concise, personalized cover letters.",
				},
				{
					"role": "user",
					"content": prompt,
				},
			],
		}

		if _MODEL_NAME in _MODELS_WITH_SAMPLING:
			request_params["temperature"] = _TEMPERATURE
			request_params["top_p"] = _TOP_P

		logger.info(f"Calling OpenAI API with model {_MODEL_NAME}")
		response = client.responses.create(**request_params)
		logger.info("Cover letter generated successfully")

		return response.output_text.strip()
	except Exception as e:
		logger.error(f"Failed to generate cover letter: {e}", exc_info=True)
		raise


def generate_filename(job_description: str) -> str:
    logger.info("Generating dynamic filename")
    try:
        client = _get_client()
        logger.info(f"Calling API with model {_FILENAME_MODEL}")
        response = client.responses.create(
            model=_FILENAME_MODEL,
            max_output_tokens=_FILENAME_MAX_TOKENS,
            input=[
                {
                    "role": "system",
                    "content": _FILENAME_PROMPT,
                },
                {
                    "role": "user",
                    "content": f"Generate a filename for a cover letter for this job:\n\n{job_description[:1000]}",
                },
            ],
        )
        logger.info("API response received")
        raw = response.output_text.strip().lower()
        logger.debug(f"Raw filename from API: '{raw}'")

        if not raw:
            logger.warning("Empty response from filename API, using default")
            return "cover_letter"

        sanitized = "".join(c if c.isalnum() or c == "_" else "_" for c in raw)
        sanitized = "_".join(part for part in sanitized.split("_") if part)  # collapse multiple underscores
        final_filename = sanitized[:40] or "cover_letter"
        logger.info(f"Generated filename: '{final_filename}'")
        return final_filename
    except Exception as e:
        logger.error(f"Filename generation failed: {e}", exc_info=True)
        logger.info("Using default filename")
        return "cover_letter"
