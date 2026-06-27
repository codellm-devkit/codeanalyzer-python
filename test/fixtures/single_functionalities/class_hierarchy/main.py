"""Class hierarchy patterns.

Exercises:
- Abstract base class (abc.ABC + @abstractmethod)
- Multiple inheritance and MRO
- super() in __init__ and regular methods
- @classmethod as factory
- @staticmethod utility
- __init_subclass__ hook
- Dynamic dispatch / polymorphism
"""
from abc import ABC, abstractmethod
from typing import List


# ---------------------------------------------------------------------------
# 1. Abstract base class
# ---------------------------------------------------------------------------

class Animal(ABC):
    _registry: List["Animal"] = []

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

    def __init__(self, name: str):
        self.name = name
        Animal._registry.append(self)

    @abstractmethod
    def speak(self) -> str:
        ...

    @classmethod
    def create(cls, name: str) -> "Animal":
        return cls(name)

    @staticmethod
    def kingdom() -> str:
        return "Animalia"

    def describe(self) -> str:
        return f"{self.name} says: {self.speak()}"


# ---------------------------------------------------------------------------
# 2. Concrete subclasses
# ---------------------------------------------------------------------------

class Dog(Animal):
    def __init__(self, name: str):
        super().__init__(name)

    def speak(self) -> str:
        return "Woof"

    def fetch(self, item: str) -> str:
        return f"{self.name} fetches {item}"


class Cat(Animal):
    def __init__(self, name: str):
        super().__init__(name)

    def speak(self) -> str:
        return "Meow"

    def purr(self) -> str:
        return f"{self.name} purrs"


# ---------------------------------------------------------------------------
# 3. Multiple inheritance + MRO
# ---------------------------------------------------------------------------

class Swimmer(ABC):
    @abstractmethod
    def swim(self) -> str:
        ...


class Duck(Animal, Swimmer):
    def __init__(self, name: str):
        super().__init__(name)

    def speak(self) -> str:
        return "Quack"

    def swim(self) -> str:
        return f"{self.name} paddles"


# ---------------------------------------------------------------------------
# 4. Deep inheritance chain with super() method call
# ---------------------------------------------------------------------------

class PoliceDog(Dog):
    def __init__(self, name: str, badge: int):
        super().__init__(name)
        self.badge = badge

    def speak(self) -> str:
        base_bark = super().speak()
        return f"{base_bark} (K9 unit #{self.badge})"


class RescuePoliceDog(PoliceDog):
    def __init__(self, name: str, badge: int, specialty: str):
        super().__init__(name, badge)
        self.specialty = specialty

    def speak(self) -> str:
        base = super().speak()
        return f"{base} [{self.specialty}]"


# ---------------------------------------------------------------------------
# 5. @classmethod factory pattern
# ---------------------------------------------------------------------------

class Config:
    def __init__(self, host: str, port: int, debug: bool = False):
        self.host = host
        self.port = port
        self.debug = debug

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        return cls(
            host=d.get("host", "localhost"),
            port=int(d.get("port", 8080)),
            debug=bool(d.get("debug", False)),
        )

    @classmethod
    def development(cls) -> "Config":
        return cls(host="127.0.0.1", port=5000, debug=True)

    @classmethod
    def production(cls) -> "Config":
        return cls(host="0.0.0.0", port=80, debug=False)

    @staticmethod
    def validate_port(port: int) -> bool:
        return 1 <= port <= 65535


# ---------------------------------------------------------------------------
# 6. Dynamic dispatch via polymorphism
# ---------------------------------------------------------------------------

def process_animals(animals: List[Animal]) -> List[str]:
    return [a.describe() for a in animals]


def make_sound_twice(animal: Animal) -> str:
    return f"{animal.speak()} {animal.speak()}"


# ---------------------------------------------------------------------------
# 7. Driver
# ---------------------------------------------------------------------------

def main():
    dog = Dog.create("Rex")
    cat = Cat.create("Whiskers")
    duck = Duck.create("Donald")
    k9 = PoliceDog("Buddy", badge=42)
    elite = RescuePoliceDog("Max", badge=99, specialty="avalanche")

    descriptions = process_animals([dog, cat, duck, k9, elite])
    sounds = [make_sound_twice(a) for a in [dog, cat]]

    cfg_dev = Config.development()
    cfg_prod = Config.production()
    cfg_custom = Config.from_dict({"host": "10.0.0.1", "port": "9090"})

    valid = Config.validate_port(cfg_dev.port)
    kingdom = Animal.kingdom()

    return descriptions, sounds, cfg_dev, cfg_prod, cfg_custom, valid, kingdom


if __name__ == "__main__":
    main()
