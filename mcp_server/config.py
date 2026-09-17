"""Shared config: loads .env, provides a Neo4j driver and an OpenAI client
pointed at the GraphAcademy proxy."""

import os
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

NEO4J_URI = os.environ["NEO4J_URI"]
NEO4J_USERNAME = os.environ["NEO4J_USERNAME"]
NEO4J_PASSWORD = os.environ["NEO4J_PASSWORD"]
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "neo4j")

OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
OPENAI_BASE_URL = os.environ.get("base_URL") or os.environ.get("BASE_URL")
MODEL_NAME = os.environ.get("MODEL_NAME", "gpt-4o-mini")

# Separate real OpenAI key for embeddings (the GraphAcademy proxy key above is
# chat-completions only). Hits api.openai.com directly, no base_url override.
EMBEDDINGS_API_KEY = os.environ["OPENAI_EMBEDDINGS_MODEL_KEY"]
EMBEDDINGS_MODEL = os.environ.get("openai_embeddings_model", "text-embedding-3-large")
EMBEDDING_DIMENSIONS = 3072  # text-embedding-3-large native dimension


def get_driver():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USERNAME, NEO4J_PASSWORD))


def get_llm_client() -> OpenAI:
    return OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)


def get_embeddings_client() -> OpenAI:
    return OpenAI(api_key=EMBEDDINGS_API_KEY)
