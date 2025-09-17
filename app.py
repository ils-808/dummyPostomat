# FastAPI locker (postomat) demo with intentional bugs
# ----------------------------------------------------
# Requirements:
#   fastapi==0.111.0
#   uvicorn==0.30.0

from __future__ import annotations

import uuid
from enum import Enum
from typing import Dict, List, Optional, Literal

from fastapi import FastAPI, Header, HTTPException, Depends
from pydantic import BaseModel
from fastapi.responses import JSONResponse

Size = Literal["S", "M", "L"]

class CellStatus(str, Enum):
    FREE = "FREE"          # Свободна
    RESERVED = "RESERVED"  # Зарезервирована
    OCCUPIED = "OCCUPIED"  # Занята
    RETURN_PENDING = "RETURN_PENDING"  # Ожидает возврата

class Cell(BaseModel):
    id: str
    size: Size
    status: CellStatus = CellStatus.FREE

class OrderStatus(str, Enum):
    CREATED = "CREATED"   # Создан
    STORED = "STORED"     # Положен в ячейку
    PICKED = "PICKED"     # Забран пользователем
    EXPIRED = "EXPIRED"   # Протух
    RETURNED = "RETURNED" # Возвращён курьером

class Order(BaseModel):
    id: str
    code: str
    sku: str
    item_size: Size
    status: OrderStatus = OrderStatus.CREATED
    cell_id: Optional[str] = None
    expired_marked: bool = False
    client_open_count: int = 0

class Seed(BaseModel):
    cells: List[Cell]
    items: Dict[str, Size]

class CreateOrderBody(BaseModel):
    sku: str

class CreateOrderResp(BaseModel):
    orderId: str
    code: str

class DepositBody(BaseModel):
    orderId: str

class DepositResp(BaseModel):
    cellId: str
    accepted: bool

class PickupBody(BaseModel):
    orderId: str
    code: str

class PickupResp(BaseModel):
    opened: bool

class CollectResp(BaseModel):
     opened: bool

class ReturnExpireBody(BaseModel):
    orderId: str

class ReturnCollectBody(BaseModel):
    orderId: str

class CellsResp(BaseModel):
    cells: List[Cell]

class SeedItem(BaseModel):
    sku: str
    size: Size

class SeedCell(BaseModel):
    id: str
    size: Size

class SeedResp(BaseModel):
    seedKey: str
    cells: List[SeedCell]
    items: List[SeedItem]

app = FastAPI(
    title="Демо постамат (FastAPI)",
    version="3.1.0",
    description=(
        "QA постамат\n\n"
        "Содержит преднамеренные баги\n\n"
        
        #"Используйте заголовок 'X-Seed-Key', который возвращается из /seed для начала работы."
        "Сначала вызовите /seed для получения X-Seed-Key, затем используйте его в заголовке."
    ),
)

STORE: Dict[str, Dict[str, object]] = {}

def get_ctx(x_seed_key: Optional[str] = Header(None, alias="X-Seed-Key")) -> Dict[str, object]:
    if not x_seed_key:
        raise HTTPException(status_code=400, detail="Необходимо указать заголовок X-Seed-Key")
    ctx = STORE.get(x_seed_key)
    if ctx is None:
        raise HTTPException(status_code=400, detail="Seed для данного X-Seed-Key не найден. Сначала вызовите /seed")
    return ctx

@app.post("/seed", response_model=SeedResp, summary="Инициализация данных", description="Возвращает X-Seed-Key, список ячеек и товаров.")
def seed():
    key = str(uuid.uuid4())
    cells = [
        Cell(id="C1", size="L"),
        Cell(id="C2", size="M"),
        Cell(id="C3", size="S"),
        Cell(id="C4", size="L"),
        Cell(id="C5", size="M"),
        Cell(id="C6", size="S"),
    ]
    items_map: Dict[str, Size] = {
        "Samsung TV": "L",
        "Sony Playstation": "M",
        "iPhone 17 Pro": "S",
        "LEGO Star Wars": "L",
        "Apple AirPods Pro 2": "M",
        "QA Job Offer": "S",
    }
    STORE[key] = {"seed": Seed(cells=cells, items=items_map), "orders": {}}
    return SeedResp(
        seedKey=key,
        cells=[SeedCell(id=c.id, size=c.size) for c in cells],
        items=[SeedItem(sku=sku, size=size) for sku, size in items_map.items()],
    )

@app.post("/orders", response_model=CreateOrderResp, summary="Создание заказа", description="Создаёт заказ на указанный SKU. Возвращает orderId и PIN-код (code).")
def create_order(body: CreateOrderBody, ctx = Depends(get_ctx)):
    seed: Seed = ctx["seed"]  # type: ignore
    orders: Dict[str, Order] = ctx["orders"]  # type: ignore

    if body.sku not in seed.items:
        raise HTTPException(status_code=404, detail="Неизвестный SKU")

    order_id = str(uuid.uuid4())
    code = str(uuid.uuid4()).split("-")[0].upper()

    order = Order(
        id=order_id,
        code=code,
        sku=body.sku,
        item_size=seed.items[body.sku],
    )
    orders[order_id] = order
    return CreateOrderResp(orderId=order_id, code=code)

@app.post("/deposit", response_model=DepositResp, summary="Помещение посылки", description="Курьер помещает заказ в свободную ячейку.")
def deposit(body: DepositBody, ctx = Depends(get_ctx)):
    seed: Seed = ctx["seed"]  # type: ignore
    orders: Dict[str, Order] = ctx["orders"]  # type: ignore

    order = orders.get(body.orderId)
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    if order.status != OrderStatus.CREATED:
        raise HTTPException(status_code=409, detail="Заказ должен быть в статусе CREATED для помещения")

    # БАГ #1: Нет проверки совместимости размеров — берём первую свободную ячейку, игнорируя размер товара
    free_cell = next((c for c in seed.cells if c.status == CellStatus.FREE), None)
    if not free_cell:
        raise HTTPException(status_code=409, detail="Нет свободных ячеек")

    # курьер не участвует в подсчёте открытий клиента
    free_cell.status = CellStatus.OCCUPIED

    order.cell_id = free_cell.id
    order.status = OrderStatus.STORED

    return DepositResp(cellId=free_cell.id, accepted=True)

@app.post(
    "/pickup",
    response_model=PickupResp,
    summary="Забор посылки",
    description="Получатель вводит PIN-код и забирает заказ."
)
def pickup(body: PickupBody, ctx = Depends(get_ctx)):
    seed: Seed = ctx["seed"]  # type: ignore
    orders: Dict[str, Order] = ctx["orders"]  # type: ignore

    order = orders.get(body.orderId)
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")

    # Разрешаем открывать, даже если заказ уже PICKED — чтобы баг «бесконечные открытия» сохранялся
    if order.status not in (OrderStatus.STORED, OrderStatus.PICKED):
        raise HTTPException(status_code=409, detail="Заказ не в подходящем статусе для выдачи")

    if body.code != order.code:
        raise HTTPException(status_code=403, detail="Неверный PIN-код")

    if order.client_open_count == 0:
        cell = _get_cell_by_id(seed, order.cell_id)

    # БАГ #2: Лимит открытий клиентом (≤2) не применяется — можно открывать бесконечно
    order.client_open_count += 1

    # На первом открытии должны бы перевести заказ в PICKED и освободить ячейку
    if order.client_open_count == 1:
        # БАГ #4: Статус заказа не меняется на PICKED после успешного pickup
        # order.status = OrderStatus.PICKED
        cell.status = CellStatus.FREE
        order.cell_id = None

    # Дальше (3-е и более) — всё равно opened=True, несмотря на то, что заказ уже PICKED и ячейка FREE.
    return PickupResp(opened=True)

@app.post("/return/expire", summary="Протухание заказа", description="Помечает заказ как EXPIRED и переводит ячейку в RETURN_PENDING.")
def return_expire(body: ReturnExpireBody, ctx = Depends(get_ctx)):
    seed: Seed = ctx["seed"]  # type: ignore
    orders: Dict[str, Order] = ctx["orders"]  # type: ignore

    order = orders.get(body.orderId)
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    # БАГ #5: Разрешаем повторное "протухание" — допускаем EXPIRED в допустимых статусах
    # if order.expired_marked:
    #     raise HTTPException(status_code=409, detail="Заказ уже был протухшим")
    # БАГ #5: Разрешаем повторное "протухание" — допускаем EXPIRED в допустимых статусах
    if order.status not in (OrderStatus.STORED, OrderStatus.EXPIRED): # вынес OrderStatus.CREATED,
        raise HTTPException(status_code=409, detail="Заказ не в подходящем статусе для протухания")
    if not order.cell_id:
        raise HTTPException(status_code=409, detail="Невозможно сделать протухшим заказ без ячейки")

    order.status = OrderStatus.EXPIRED
    # БАГ #5: Разрешаем повторное "протухание" — допускаем EXPIRED в допустимых статусах
    order.expired_marked = True
    if order.cell_id:
        cell = _get_cell_by_id(seed, order.cell_id)
        cell.status = CellStatus.RETURN_PENDING
    return {"expiredOrder": order.id}

@app.post("/return/collect", response_model=CollectResp, summary="Возврат курьером", description="Курьер открывает ячейку и забирает невостребованную посылку")
def return_collect(body: ReturnCollectBody, ctx = Depends(get_ctx)):
    seed: Seed = ctx["seed"]  # type: ignore
    orders: Dict[str, Order] = ctx["orders"]  # type: ignore

    order = orders.get(body.orderId)
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    if order.status != OrderStatus.EXPIRED:
        raise HTTPException(status_code=409, detail="Заказ должен быть в статусе EXPIRED")

    cell = _get_cell_by_id(seed, order.cell_id)
    order.cell_id = None

    # курьерское открытие не учитываем в лимите клиента
    order.status = OrderStatus.RETURNED

    # БАГ #3: ячейка остаётся в RETURN_PENDING, не освобождаем
    return CollectResp(opened=True)

@app.get("/cells", response_model=CellsResp, summary="Список ячеек", description="Возвращает текущее состояние всех ячеек.")
def list_cells(ctx = Depends(get_ctx)):
    seed: Seed = ctx["seed"]  # type: ignore
    return CellsResp(cells=seed.cells)

@app.get("/orders/{order_id}", response_model=Order, summary="Информация о заказе", description="Возвращает полную информацию по заказу.")
def get_order(order_id: str, ctx = Depends(get_ctx)):
    orders: Dict[str, Order] = ctx["orders"]  # type: ignore
    order = orders.get(order_id)
    if not order:
        raise HTTPException(status_code=404, detail="Заказ не найден")
    return order

@app.get("/", summary="Корень API", description="Служебная информация о сервисе.")
def root():
    payload = {
        "service": "postomat-demo-fastapi",
        "version": "3.1.0",
        "docs": "/docs",
        "openapi": "/openapi.json",
        "note": "Сначала вызовите /seed для получения X-Seed-Key, затем используйте его в заголовке."
    }
    return JSONResponse(payload, media_type="application/json; charset=utf-8")

def _get_cell_by_id(seed: Seed, cell_id: Optional[str]) -> Cell:
    if not cell_id:
        raise HTTPException(status_code=409, detail="У заказа нет ячейки")
    for c in seed.cells:
        if c.id == cell_id:
            return c
    raise HTTPException(status_code=404, detail="Ячейка не найдена")
