# Nostra MySQL CRUD API

Einfalt `FastAPI` verkefni sem tengist MySQL og býður upp á generic CRUD fyrir töflur í einu schema.

## Deploy á Raspberry Pi

### 1. Byggja og pusha á Docker Hub

```bash
docker build -t sogestsson/nostra-mysql-demo-api:latest .
docker login
docker push sogestsson/nostra-mysql-demo-api:latest
```

### 2. Á Raspberry Pi — draga nýja image

```bash
docker pull sogestsson/nostra-mysql-demo-api:latest
```

### 3. Stoppa gamla og keyra nýja

```bash
docker stop drill-db-api
docker rm drill-db-api
docker run -d \
  --name drill-db-api \
  --restart unless-stopped \
  --add-host=host.docker.internal:host-gateway \
  -p 8001:8000 \
  -e MYSQL_HOST=host.docker.internal \
  -e MYSQL_PORT=4406 \
  -e MYSQL_USER=root \
  -e MYSQL_PASSWORD=Superman \
  -e MYSQL_DATABASE=smart_stock \
  sogestsson/nostra-mysql-demo-api:latest
```

API verður aðgengilegt á `http://raspberrypi.local:8001/docs`

Sjá logs:

```bash
docker logs -f drill-db-api
```

## Keyrsla með Docker (local þróun)

```bash
docker compose up --build
```

Keyra í bakgrunni:

```bash
docker compose up --build -d
```

Sjá logs:

```bash
docker compose logs -f
```

## Keyrsla án Docker

```bash
pipenv install
```

```bash
MYSQL_HOST=raspberrypi.local \
MYSQL_PORT=4406 \
MYSQL_USER=root \
MYSQL_PASSWORD=Superman \
MYSQL_DATABASE=smart_stock \
pipenv run uvicorn app.main:app --reload
```

Swagger docs:

- `http://localhost:8000/docs`

## Endapunktar

- `GET /health`
- `GET /tables`
- `GET /sim-input/{item_id}`
- `GET /forecast-input/{item_id}`
- `GET /tables/{table_name}/rows?limit=100&offset=0`
- `POST /tables/{table_name}/rows`
- `GET /tables/{table_name}/rows/{row_id}`
- `PUT /tables/{table_name}/rows/{row_id}`
- `DELETE /tables/{table_name}/rows/{row_id}`

## Ath

- `GET/PUT/DELETE` á staka röð gera ráð fyrir að taflan hafi nákvæmlega einn primary key dálk.
- `POST` sleppir `auto_increment` dálkum sjálfkrafa.
- Svör eru skiluð sem JSON objects beint úr gagnagrunninum.
- `GET /sim-input/{item_id}` skilar topp-level JSON með lyklunum `sim_input_his`, `sim_rio_items` og `sim_rio_item_details`.
- `GET /sim-input/{item_id}` skilar líka `sim_rio_on_order`, `number_of_days`, `number_of_simulations` og `service_level`.
- `GET /sim-input/{item_id}` styður query params: `number_of_days` (default `900`), `number_of_simulations` (default `1000`), `service_level` (default `0.95`), `start_day`, `end_day`.
- Ef `end_day` er ekki gefinn, endar `sim_input_his` alltaf á deginum í dag og allir dagar eftir síðustu hreyfingu fá `actual_sale = 0`.
- `GET /forecast-input/{item_id}` les úr `item_histories` og skilar forecast request payload með `sim_input_his`, `forecast_periods`, `mode`, `local_model`, `season_length` og `freq`.
- `GET /forecast-input/{item_id}` styður query params: `forecast_periods` (default `30`), `mode` (default `local`), `local_model` (default `auto_arima`), `season_length` (default `7`), `freq` (default `D`), `start_day`, `end_day`.
