import asyncio
from bot.memory.retriver import MemoryRetriever

async def retriver():
    retriever = MemoryRetriever()
    user_id = '1'
    app_id = '3'
    query = '页面'
    results = await retriever.retrieve(user_id=user_id, app_id=app_id, query=query, is_hybrid=True)
    for r in results:
        print(f'id: {r.id}, text: {r.text}, score: {r.score:.4f}, type: {r.type}')


if __name__ == "__main__":
    asyncio.run(retriver())
