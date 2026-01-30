import asyncio
from BOT.main import start_bot1
from BOT1.main import start_bot2
from BOT3.main import start_bot3  # <- 3rd bot import

async def main():
    # 3 bots ek saath run honge
    await asyncio.gather(
        start_bot1(),
        start_bot2(),
        start_bot3()   # <- 3rd bot added
    )
    
    # Process ko alive rakhe indefinitely
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
