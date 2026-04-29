from typing import List, Dict, Any

# from langchain_core.messages import SystemMessage, HumanMessage
from openai import OpenAI
from bot.utils.log_utils import log
from bot.utils.config import load_config

class EmbeddingClient:

    def __init__(self):
        config = load_config()
        embedding_config = config.embedding
        provider_config = config.providers.dashscope
        api_key = embedding_config.api_key or provider_config.api_key or "invalid_key"
        base_url = embedding_config.api_base or provider_config.api_base or "https://api.openai.com/v1"

        self.client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        self.model_name = embedding_config.model_name or config.get_default_agent().resolved_model_name
        self.max_text_length = embedding_config.max_text_length
        self.api_timeout_seconds = embedding_config.api_timeout_seconds
        log.info(f"using model:{self.model_name}")

    async def embed(self, input_text) -> List[float]:

        completion = self.client.embeddings.create(
            model=self.model_name,
            input=input_text
        )
        return completion.data[0].embedding

    async def batch_encode(self, texts: List[str]) -> list[list[float]]:
        """
        批量编码文本为向量
        Args:         texts: 文本列表
        Returns:      向量数组，形状为 (len(texts), embedding_dim)
        """
        if not texts:
            return []

        texts = [text[: self.max_text_length] for text in texts]

        try:
            batch_size = 10
            embeddings = []
            for i in range(0, len(texts), batch_size):
                batch_texts = texts[i:i + batch_size]
                response = self.client.embeddings.create(
                    model=self.model_name,
                    input=batch_texts,
                    timeout=self.api_timeout_seconds,
                )
                response_data = response.data
                if not response_data:
                    raise ValueError("文本编码失败: 未返回任何数据")

                for embedding in response_data:
                    embeddings.append(embedding.embedding)
            return embeddings

        except Exception as e:
            import traceback
            log.error(f"文本编码失败: {e}\n{traceback.format_exc()}")
            raise Exception(f"文本编码失败: {e}") from e


# class BM25Embedding:
#     from fastembed import SparseTextEmbedding
#     def __init__(self):
#         self._model = SparseTextEmbedding("Qdrant/bm25")
#
#     def encode_text_batch(self, texts: list[str]) -> list[dict]:
#         embeddings = self._model.embed(texts)
#         return [embedding.as_object() for embedding in embeddings]
if __name__ == '__main__':
    pass
    
