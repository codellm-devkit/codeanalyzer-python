"""Async/await patterns.

Exercises:
- async def + await
- asyncio.gather (concurrent tasks)
- Async generator (async def + yield)
- async for loop consuming an async generator
- Async context manager (__aenter__ / __aexit__)
- async with block
- Nested awaits / task composition
"""
import asyncio
from typing import AsyncGenerator, List


# ---------------------------------------------------------------------------
# 1. Basic async function
# ---------------------------------------------------------------------------

async def fetch_data(url: str) -> str:
    await asyncio.sleep(0)
    return f"data:{url}"


async def process_url(url: str) -> str:
    raw = await fetch_data(url)
    return raw.upper()


# ---------------------------------------------------------------------------
# 2. Concurrent tasks with asyncio.gather
# ---------------------------------------------------------------------------

async def fetch_all(urls: List[str]) -> List[str]:
    tasks = [process_url(url) for url in urls]
    return await asyncio.gather(*tasks)


# ---------------------------------------------------------------------------
# 3. Async generator
# ---------------------------------------------------------------------------

async def generate_chunks(data: str, size: int) -> AsyncGenerator[str, None]:
    for i in range(0, len(data), size):
        await asyncio.sleep(0)
        yield data[i : i + size]


# ---------------------------------------------------------------------------
# 4. async for consuming an async generator
# ---------------------------------------------------------------------------

async def collect_chunks(data: str, size: int) -> List[str]:
    chunks: List[str] = []
    async for chunk in generate_chunks(data, size):
        chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# 5. Async context manager
# ---------------------------------------------------------------------------

class AsyncResource:
    def __init__(self, name: str):
        self.name = name
        self._open = False

    async def __aenter__(self) -> "AsyncResource":
        await asyncio.sleep(0)
        self._open = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> bool:
        await asyncio.sleep(0)
        self._open = False
        return False

    async def read(self) -> str:
        if not self._open:
            raise RuntimeError("Resource not open")
        return f"content of {self.name}"


# ---------------------------------------------------------------------------
# 6. async with
# ---------------------------------------------------------------------------

async def read_resource(name: str) -> str:
    async with AsyncResource(name) as res:
        return await res.read()


# ---------------------------------------------------------------------------
# 7. Task composition
# ---------------------------------------------------------------------------

async def pipeline(urls: List[str], resource_name: str) -> dict:
    fetched = await fetch_all(urls)
    chunks = await collect_chunks("hello world async", size=5)
    content = await read_resource(resource_name)
    return {
        "fetched": fetched,
        "chunks": chunks,
        "content": content,
    }


# ---------------------------------------------------------------------------
# 8. Driver
# ---------------------------------------------------------------------------

async def async_main() -> dict:
    urls = [
        "http://example.com/a",
        "http://example.com/b",
        "http://example.com/c",
    ]
    return await pipeline(urls, resource_name="config.json")


def main():
    return asyncio.run(async_main())


if __name__ == "__main__":
    main()
