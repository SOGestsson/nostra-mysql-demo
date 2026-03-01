# Nostra MySQL CRUD API

Einfalt `FastAPI` verkefni sem tengist MySQL og býður upp á generic CRUD fyrir töflur í einu schema.

## Uppsetning

```bash
pipenv install
```

## Keyrsla

```bash
MYSQL_HOST=raspberrypi.local \
MYSQL_PORT=4406 \
MYSQL_USER=root \
MYSQL_PASSWORD=Superman \
MYSQL_DATABASE=drill_project \
pipenv run uvicorn app.main:app --reload
```

Swagger docs:

- `http://127.0.0.1:8000/docs`

## Endapunktar

- `GET /health`
- `GET /tables`
- `GET /sim-input/{item_id}`
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
