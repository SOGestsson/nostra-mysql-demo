# Nostra MySQL CRUD API

FastAPI REST API sem tengist MySQL og býður upp á generic CRUD, JWT auth, admin-stjórnun og domain endpoints fyrir Nostradamus/simulation vinnu.

## Tæknistakkur

- Python 3.13, FastAPI, Uvicorn, Pydantic v2
- MySQL (`mysql-connector-python`)
- JWT auth (bcrypt + PyJWT) gegn `nostradamus_master` gagnagrunni

## Umhverfisbreytur

| Breyta | Lýsing | Sjálfgefið |
|---|---|---|
| `MYSQL_HOST` | Aðal DB host | `raspberrypi.local` |
| `MYSQL_PORT` | Aðal DB port | `4406` |
| `MYSQL_USER` | DB notandi | `root` |
| `MYSQL_PASSWORD` | DB lykilorð | *(í kóða)* |
| `MYSQL_DATABASE` | Sjálfgefin DB | `smart_stock` |
| `MASTER_DB_HOST` | Master DB (auth, tengingar) | `MYSQL_HOST` |
| `MASTER_DB_PORT` | Master DB port | `MYSQL_PORT` |
| `MASTER_DB_USER` | Master DB notandi | `MYSQL_USER` |
| `MASTER_DB_PASSWORD` | Master DB lykilorð | `MYSQL_PASSWORD` |
| `JWT_SECRET` | Undirritun JWT | *(í kóða — breyttu í prod!)* |

Flestar aðgerðir styðja `?db=<database_name>` til að velja gagnagrunn úr `nostradamus_master.database_connections`.

## Keyrsla

### Docker (local)

```bash
cp .env.example .env   # stilltu MYSQL_PASSWORD og JWT_SECRET
docker compose up --build
```

Swagger: `http://localhost:8000/docs`

### Án Docker

```bash
pipenv install
MYSQL_HOST=raspberrypi.local MYSQL_PORT=4406 MYSQL_USER=root \
MYSQL_PASSWORD=<lykilorð> MYSQL_DATABASE=smart_stock \
pipenv run uvicorn app.main:app --reload
```

## Deploy á Raspberry Pi

```bash
docker build -t sogestsson/nostra-mysql-demo-api:latest .
docker push sogestsson/nostra-mysql-demo-api:latest
```

Á Pi:

```bash
docker pull sogestsson/nostra-mysql-demo-api:latest
docker stop drill-db-api && docker rm drill-db-api
docker run -d \
  --name drill-db-api \
  --restart unless-stopped \
  --add-host=host.docker.internal:host-gateway \
  -p 8001:8000 \
  -e MYSQL_HOST=host.docker.internal \
  -e MYSQL_PORT=4406 \
  -e MYSQL_USER=root \
  -e MYSQL_PASSWORD=<lykilorð> \
  -e MYSQL_DATABASE=smart_stock \
  -e JWT_SECRET=<stakt-leynilykil> \
  sogestsson/nostra-mysql-demo-api:latest
```

API: `http://raspberrypi.local:8001/docs`

## Endapunktar

### Heilsa og gagnagrunnar

- `GET /health`
- `GET /databases`
- `GET /tables?db=`
- `GET /tables/{table_name}/columns?db=`
- `GET /tables/{table_name}/ddl?db=`
- `POST /tables/{table_name}/ddl?db=`

### CRUD

- `GET /tables/{table_name}/rows?limit=&offset=&db=`
- `POST /tables/{table_name}/rows?db=`
- `GET /tables/{table_name}/rows/{row_id}?db=`
- `PUT /tables/{table_name}/rows/{row_id}?db=`
- `DELETE /tables/{table_name}/rows/{row_id}?db=`

### Auth og admin

- `POST /auth/register`
- `POST /auth/login`
- `GET /admin/users`
- `POST /admin/users`
- `DELETE /admin/users/{user_id}`
- `GET /db-config/{db_name}`
- `PUT /admin/db-config/{db_name}`
- `GET /user/db-config/{db_name}`
- `PUT /user/db-config/{db_name}`

### Simulation og forecast

- `GET /sim-input/{item_id}?db=&number_of_days=&number_of_simulations=&service_level=`
- `GET /forecast-input/{item_id}?db=&forecast_periods=&mode=&local_model=&season_length=&freq=`
- `GET /sim-prep?db=&item_ids=`
- `POST /sim-result?db=`
- `POST /purchase-suggestions?db=`

### UI config og lookup

- `GET /db-config/{db_name}` — admin UI stillingar (krefst JWT)
- `GET /user/db-config/{db_name}` — sameinað admin + notenda stillingar
- `PUT /admin/db-config/{db_name}` — setja editable dálka, columnEditors, catalogTable
- `GET /tables/{table_name}/columns?db=` — dálkheiti + `data_type` metadata
- `GET /lookup-options?db=&table=&value_column=&label_column=` — dropdown valmöguleikar

Dæmi um consumables config: [`config/consumables_ui_config.example.json`](config/consumables_ui_config.example.json)

```bash
ADMIN_TOKEN=<admin-jwt> DB_NAME=consumables ./scripts/set_consumables_config.sh
```

### Annað

- `GET /vendor-names?db=`
- `PUT /items/{item_id}/vendor-override?db=`

## Athugasemdir

- `GET/PUT/DELETE` á staka röð gera ráð fyrir nákvæmlega einn primary key dálk.
- `POST` sleppir `auto_increment` dálkum sjálfkrafa.
- `GET /sim-input/{item_id}` skilar `sim_input_his`, `sim_rio_items`, `sim_rio_item_details`, `sim_rio_on_order`, `number_of_days`, `number_of_simulations`, `service_level`.
- Ef `end_day` vantar endar `sim_input_his` á deginum í dag; dagar eftir síðustu hreyfingu fá `actual_sale = 0`.
- Krefst `nostradamus_master` schema (users, database_connections, db_ui_config, user_ui_config).
