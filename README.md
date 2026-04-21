# Cremacuadrado API — Backend

API REST para el ecommerce de **Cremacuadrado**, una tienda de cremas de pistacho artesanales. Construida con **FastAPI**, **SQLAlchemy** y **PostgreSQL**.

---

## Tecnologías

| Capa | Tecnología |
|---|---|
| Framework | FastAPI 0.109 |
| ORM | SQLAlchemy 2.0 |
| Base de datos | PostgreSQL (Supabase en staging) |
| Migraciones | Alembic 1.13 |
| Autenticación | JWT (python-jose + passlib) |
| Validación | Pydantic v2 |
| Servidor | Uvicorn |
| Python | 3.11+ |

---

## Estructura del proyecto

```
backend/
├── app/
│   ├── main.py              # Entry point, CORS, lifespan
│   ├── config.py            # Configuración mediante variables de entorno
│   ├── api/
│   │   ├── deps.py          # Dependencias comunes (DB session, usuario actual)
│   │   └── v1/
│   │       ├── auth.py      # Registro, login, refresh, reset de contraseña
│   │       ├── products.py  # Catálogo, categorías, reseñas
│   │       ├── cart.py      # Carrito de compra
│   │       ├── checkout.py  # Proceso de pago
│   │       ├── orders.py    # Historial de pedidos
│   │       ├── users.py     # Perfil y direcciones
│   │       ├── blog.py      # Artículos del blog
│   │       └── admin.py     # Panel de administración
│   ├── models/              # Modelos SQLAlchemy
│   ├── schemas/             # Schemas Pydantic (request/response)
│   ├── services/
│   │   └── email.py         # Servicio de email (MVP: logs a consola)
│   └── utils/
│       └── security.py      # Hash, JWT, tokens
├── migrations/              # Migraciones Alembic
├── static/
│   └── images/products/     # Imágenes de productos
├── requirements.txt
├── alembic.ini
└── setup_database.py
```

---

## Endpoints principales

Todos los endpoints están bajo el prefijo `/api/v1`.

| Módulo | Prefijo | Descripción |
|---|---|---|
| Auth | `/auth` | Registro, login, refresh token, recuperar contraseña |
| Productos | `/products` | Listado, detalle, categorías, reseñas |
| Carrito | `/cart` | Añadir, actualizar y eliminar ítems |
| Checkout | `/checkout` | Calcular totales y confirmar pedido |
| Pedidos | `/orders` | Historial y detalle de pedidos del usuario |
| Usuarios | `/users` | Perfil, direcciones de envío |
| Blog | `/blog` | Artículos y categorías del blog |
| Admin | `/admin` | Gestión de productos, pedidos y usuarios |

Con `DEBUG=True`, la documentación interactiva está disponible en:
- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`

---

## Instalación y puesta en marcha

### 1. Clonar el repositorio

```bash
git clone https://github.com/porronxo92/cremacuadrado-back.git
cd cremacuadrado-back
```

### 2. Crear entorno virtual e instalar dependencias

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS/Linux
source venv/bin/activate

pip install -r requirements.txt
```

### 3. Configurar variables de entorno

Copia el archivo de ejemplo y rellena los valores:

```bash
cp .env.example .env
```

Variables **obligatorias**:

```env
DATABASE_URL=postgresql://user:password@host:5432/dbname
SECRET_KEY=genera-una-clave-aleatoria-larga
ADMIN_EMAIL=admin@cremacuadrado.com
ADMIN_PASSWORD=contraseña-segura
```

Genera una `SECRET_KEY` segura con:

```bash
python -c "import secrets; print(secrets.token_hex(64))"
```

### 4. Ejecutar migraciones

```bash
alembic upgrade head
```

### 5. (Opcional) Poblar la base de datos

```bash
python setup_database.py
```

### 6. Arrancar el servidor

```bash
uvicorn app.main:app --reload
```

La API queda disponible en `http://127.0.0.1:8000`.

---

## Variables de entorno

| Variable | Obligatoria | Por defecto | Descripción |
|---|---|---|---|
| `DATABASE_URL` | No | PostgreSQL local | URL de conexión a PostgreSQL |
| `SECRET_KEY` | **Sí** | — | Clave secreta para firmar JWT |
| `ALGORITHM` | No | `HS256` | Algoritmo JWT |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `30` | Expiración del access token |
| `REFRESH_TOKEN_EXPIRE_DAYS` | No | `7` | Expiración del refresh token |
| `ADMIN_EMAIL` | No | `admin@cremacuadrado.com` | Email del administrador |
| `ADMIN_PASSWORD` | **Sí** | — | Contraseña del administrador |
| `DEBUG` | No | `True` | Activa `/docs` y `/redoc` |
| `CORS_ORIGINS` | No | `["http://localhost:4200"]` | Orígenes permitidos en CORS |
| `SHIPPING_COST` | No | `4.95` | Coste de envío fijo (€) |
| `FREE_SHIPPING_THRESHOLD` | No | `50.0` | Umbral envío gratuito (€) |
| `TAX_RATE` | No | `0.21` | Tipo de IVA |
| `EMAIL_ENABLED` | No | `False` | Activa envío real de emails |
| `EMAIL_FROM` | No | `info@cremacuadrado.com` | Dirección remitente |

---

## Autenticación

La API utiliza **JWT Bearer tokens**. El flujo es:

1. `POST /api/v1/auth/register` — Crear cuenta
2. `POST /api/v1/auth/login` — Obtener `access_token` y `refresh_token`
3. Incluir el token en las peticiones protegidas:  
   `Authorization: Bearer <access_token>`
4. `POST /api/v1/auth/refresh` — Renovar el access token con el refresh token

---

## Migraciones

```bash
# Crear una nueva migración
alembic revision --autogenerate -m "descripción del cambio"

# Aplicar migraciones
alembic upgrade head

# Revertir última migración
alembic downgrade -1
```

---

## Tests

```bash
pytest
```

---

## Notas de desarrollo

- **Email:** En MVP, los emails se registran en consola. Activar `EMAIL_ENABLED=True` y configurar `SMTP_*` para envío real.
- **Imágenes:** Los archivos estáticos se sirven desde `/static/images/products/` con cabeceras de caché de 30 días.
- **Base de datos:** En desarrollo se usa PostgreSQL local. En staging/producción se utiliza Supabase con session pooler.
