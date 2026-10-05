# FastAPI 웹 API 서버 만들기 위한 프레임워크 와 HTTP 오류 응답을 만들기 위한 기능
from fastapi import FastAPI, HTTPException

# API 요청/응답 데이터 구조와 타입을 정의하기 위한 라이브러리
from pydantic import BaseModel

# 외부 REST API(DummyJSON)를 호출하기 위한 HTTP 클라이언트
import httpx

# Google Gemini API를 Python에서 사용하기 위한 공식 SDK
from google import genai

# Gemini 응답 형식 등의 세부 설정을 지정하기 위한 모듈
from google.genai import types

# Gemini 429를 정상적인 API 오류로 처리(사용량 초과)
from google.genai import errors

# .env 파일의 GEMINI_API_KEY를 환경변수로 불러오기 위한 라이브러리
from dotenv import load_dotenv
#=====================================================================
# 사용자가 /agent/order API로 보내는 자연어 요청 데이터
class AgentRequest(BaseModel):
    message: str
# Gemini가 자연어 주문에서 추출할 구조화된 주문 데이터
class ExtractedOrder(BaseModel):
    product_name: str
    quantity: int
# AI Agent Tool Calling 테스트용 요청 데이터
class AgentToolRequest(BaseModel):
    message: str
# /orders/validate API에서 사용하는 주문 검증 요청 데이터
class OrderRequest(BaseModel):
    product_id: int
    quantity: int

# .env 파일에 저장된 GEMINI_API_KEY 환경변수 로드
load_dotenv()

app = FastAPI()

# Gemini API와 통신하기 위한 Client 생성
gemini_client = genai.Client()

# 상품 정보와 주문 수량을 기준으로 주문 가능 여부를 검증하는 공통 함수
def validate_order_rules(product: dict, quantity: int):

    # 재고가 충분한지 확인
    stock_ok = quantity <= product["stock"]

    # 최소 주문 수량을 충족하는지 확인
    minimum_order_ok = quantity >= product["minimumOrderQuantity"]

    # 두 조건을 모두 만족해야 주문 가능
    order_available = stock_ok and minimum_order_ok

    # 주문 가능 여부에 따른 결과 메시지 생성
    if not stock_ok:
        message = f"재고가 부족합니다. 현재 재고는 {product['stock']}개입니다."
    elif not minimum_order_ok:
        message = f"최소 주문 수량은 {product['minimumOrderQuantity']}개입니다."
    else:
        message = "주문 검증을 통과했습니다."

    # 주문 총 금액 계산
    total_price = product["price"] * quantity

    return {
        "stock_ok": stock_ok,
        "minimum_order_ok": minimum_order_ok,
        "order_available": order_available,
        "total_price": total_price,
        "message": message
    }
    
@app.get("/")
def root():
    return {"message": "9 Dots AI Agent API"}


@app.get("/product/{product_id}")
def get_product(product_id: int):
    response = httpx.get(
        f"https://dummyjson.com/products/{product_id}",
        timeout=5.0
    )

    response.raise_for_status()

    product = response.json()

    return {
        "product_id": product["id"],
        "product_name": product["title"],
        "price": product["price"],
        "stock": product["stock"],
        "sku": product["sku"],
        "availability_status": product["availabilityStatus"],
        "minimum_order_quantity": product["minimumOrderQuantity"]
    }

@app.get("/products/search")
def search_products(q: str):
    response = httpx.get(
        "https://dummyjson.com/products/search",
        params={"q": q}
    )

    data = response.json()

    products = []

    for product in data["products"]:
        products.append({
            "product_id": product["id"],
            "product_name": product["title"],
            "price": product["price"],
            "stock": product["stock"],
            "sku": product["sku"],
            "availability_status": product["availabilityStatus"],
            "minimum_order_quantity": product["minimumOrderQuantity"]
        })

    return {
        "search_query": q,
        "products": products
    }

@app.post("/orders/validate")
def validate_order(order: OrderRequest):

    # product_id를 사용하여 외부 Products API에서 상품 조회
    response = httpx.get(
        f"https://dummyjson.com/products/{order.product_id}",
        timeout=5.0
    )
    
    response.raise_for_status()

    # 외부 API의 JSON 응답을 Python 데이터로 변환
    product = response.json()

    # 공통 주문 검증 함수 호출
    validation = validate_order_rules(product, order.quantity)

    # 상품 정보와 주문 검증 결과 반환
    return {
        "product_id": product["id"],
        "product_name": product["title"],
        "quantity": order.quantity,
        "price": product["price"],
        "total_price": validation["total_price"],
        "stock": product["stock"],
        "minimum_order_quantity": product["minimumOrderQuantity"],
        "stock_ok": validation["stock_ok"],
        "minimum_order_ok": validation["minimum_order_ok"],
        "order_available": validation["order_available"],
        "message": validation["message"]
    }

@app.post("/agent/order")
def process_order(request: AgentRequest):

    # 사용자의 자연어 주문을 Gemini에게 전달하고
    # 상품명과 주문 수량을 구조화된 데이터로 추출
    response = gemini_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=request.message,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=ExtractedOrder,
        ),
    )

    # Gemini의 Structured Output을 ExtractedOrder 객체로 변환
    order = response.parsed

    # Gemini가 추출한 상품명으로 외부 Products API 검색
    product_response = httpx.get(
        "https://dummyjson.com/products/search",
        params={"q": order.product_name}
    )

    product_data = product_response.json()

    # 검색된 상품이 없으면 404 오류 반환
    if not product_data["products"]:
        raise HTTPException(
            status_code=404,
            detail=f"상품을 찾을 수 없습니다: {order.product_name}"
        )

    # 검색 결과가 존재할 경우 첫 번째 상품 사용
    product = product_data["products"][0]
    
      # 공통 주문 검증 함수 호출
    validation = validate_order_rules(product, order.quantity)
   
    return {
        "received_message": request.message,
        "product_id": product["id"],
        "product_name": product["title"],
        "quantity": order.quantity,
        "price": product["price"],
        "stock": product["stock"],
        "minimum_order_quantity": product["minimumOrderQuantity"],
        "total_price": validation["total_price"],
        "stock_ok": validation["stock_ok"],
        "minimum_order_ok": validation["minimum_order_ok"],
        "order_available": validation["order_available"],
        "message": validation["message"]
}

@app.post("/agent/tool-test")
def agent_tool_test(request: AgentToolRequest):
    # Gemini에게 주문 처리에 필요한 Tool을 제공
    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=f"""
너는 주문 처리 AI Agent다.

사용자가 상품 주문을 요청하면 반드시 다음 순서로 처리한다.

1. search_product_tool을 사용하여 상품을 검색한다.
2. 검색 결과에서 상품 ID를 확인한다.
3. validate_order_tool을 사용하여 해당 상품의 주문 가능 여부를 검증한다.
4. 주문 가능 여부는 절대로 직접 판단하지 말고 validate_order_tool의 결과만 사용한다.
5. 모든 검증이 끝난 후 사용자에게 최종 결과를 알려준다.

사용자 요청:
{request.message}
""",
            config=types.GenerateContentConfig(
                tools=[
                    search_product_tool,
                    validate_order_tool
                ]
            ),
        )

        return {
            "received_message": request.message,
            "agent_response": response.text
        }

    except errors.ClientError as e:
        if e.code == 429:
            raise HTTPException(
                status_code=503,
                detail="AI Agent API 사용량이 초과되었습니다. 잠시 후 다시 시도해주세요."
            )

        raise HTTPException(
            status_code=502,
            detail="AI Agent 서비스 호출 중 오류가 발생했습니다."
        )
    
# AI Agent가 사용할 상품 검색 Tool
def search_product_tool(query: str) -> str:
    """
    사용자가 주문하려는 상품을 검색한다.
    한국어 상품명이 들어오면 검색에 적합한 영어 키워드로 변환하여 전달해야 한다.

    Args:
        query: DummyJSON Products API에서 검색할 상품 키워드

    Returns:
        검색된 상품 정보를 문자열로 반환한다.
    """

    # Gemini가 실제로 Tool을 호출했는지 서버 콘솔에서 확인
    print(f"[TOOL CALL] search_product_tool(query={query})")

    # DummyJSON Products API에서 상품 검색
    response = httpx.get(
    "https://dummyjson.com/products/search",
    params={"q": query},
    timeout=5.0
)

    response.raise_for_status()

    # 외부 API의 JSON 응답을 Python 데이터로 변환
    data = response.json()

    # 외부 API 검색 결과 확인용 로그
    print(f"[TOOL RESULT] 검색 결과 수: {len(data['products'])}")

    if data["products"]:
        print(f"[TOOL RESULT] 첫 번째 상품: {data['products'][0]['title']}")

    # 검색 결과가 없는 경우
    if not data["products"]:
        return "검색된 상품이 없습니다."

    # 현재 테스트 단계에서는 첫 번째 검색 상품 사용
    product = data["products"][0]

    # 검색된 상품 정보를 Gemini에게 반환
    return (
        f"상품 ID: {product['id']}, "
        f"상품명: {product['title']}, "
        f"가격: {product['price']}, "
        f"재고: {product['stock']}, "
        f"최소 주문 수량: {product['minimumOrderQuantity']}"
    )

# AI Agent가 사용할 주문 검증 Tool
def validate_order_tool(product_id: int, quantity: int) -> str:
    """
    상품 ID와 주문 수량을 기준으로 주문 가능 여부를 검증한다.

    Args:
        product_id: 검증할 상품의 ID
        quantity: 사용자가 주문하려는 수량

    Returns:
        재고, 최소 주문 수량, 총 금액, 주문 가능 여부를 반환한다.
    """

    # Gemini가 실제로 Tool을 호출했는지 서버 콘솔에서 확인
    print(
        f"[TOOL CALL] validate_order_tool("
        f"product_id={product_id}, quantity={quantity})"
    )

    # 상품 ID로 실제 상품 정보 조회
    response = httpx.get(
    f"https://dummyjson.com/products/{product_id}",
    timeout=5.0
)

    response.raise_for_status()
    
    # 외부 API 응답을 Python 데이터로 변환
    product = response.json()
    
    # 기존에 만든 공통 비즈니스 규칙 재사용
    validation = validate_order_rules(product, quantity)

    return (
        f"상품명: {product['title']}, "
        f"주문 수량: {quantity}, "
        f"현재 재고: {product['stock']}, "
        f"최소 주문 수량: {product['minimumOrderQuantity']}, "
        f"총 금액: {validation['total_price']}, "
        f"주문 가능 여부: {validation['order_available']}, "
        f"결과: {validation['message']}"
    )