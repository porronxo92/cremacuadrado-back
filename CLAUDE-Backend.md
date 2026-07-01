# CLAUDE.md — Backend
## CremaCuadrado · Ecommerce de crema de pistacho manchego artesanal

---

## Resumen del Proyecto

CremaCuadrado es un ecommerce D2C (Direct-to-Consumer) de crema de pistacho manchego artesanal elaborada en Ciudad Real, Castilla-La Mancha. La web sirve a tres perfiles de cliente con embudos diferenciados:

- **B2C** — consumidor final que compra online
- **B2B Punto de venta** — tiendas gourmet y delicatessen que revenden el producto
- **B2B Ingrediente profesional** — restaurantes, pastelerías y cafés de especialidad que usan la crema como ingrediente

El backend gestiona el catálogo de productos, el carrito, el flujo de checkout con Stripe, la autenticación de usuarios, el sistema de suscripción mensual (club mensual −15%) y los formularios B2B (leads guardados en tabla propia + notificación por email a b2b@cremacuadrado.com).

### Stack tecnológico

> ⚠️ Por definir — el webmaster construye el proyecto a medida usando IA (Claude). El stack exacto (lenguaje, framework, ORM) debe confirmarse con él antes de comenzar el desarrollo.

---

## Arquitectura

> ⚠️ Por definir — estructura de carpetas y módulos pendiente de decisión del webmaster.

### Patrones conocidos

- Separación clara entre lógica B2C y B2B
- Formularios B2B guardan el lead en tabla propia (BBDD) y notifican por email a b2b@cremacuadrado.com (integración con un CRM externo tipo HubSpot queda como posible mejora futura, no implementada)
- Evento `purchase` dispara secuencias automatizadas en la herramienta de email marketing
- Variables de entorno para todas las credenciales externas (Stripe, Google OAuth)

---

## Base de Datos

BBDD: postgresql://postgres.pochhuolpjgzjjbgrlia:%3F%40%2A85PP5xam%2B%25@aws-0-eu-west-1.pooler.supabase.com:6543/postgres

### Entidades principales a modelar

| Entidad | Notas |
|---|---|
| `users` | Clientes registrados + Google OAuth |
| `products` | Pura y Crunchy (y Pistagreta cuando se lance) |
| `product_variants` | Formatos: 100g / 200g / 1kg |
| `orders` | Pedidos B2C |
| `order_items` | Líneas de pedido |
| `subscriptions` | Club mensual — suscripciones recurrentes |
| `discount_codes` | Cupones (bienvenida 10%, reseña 10%, etc.) |
| `b2b_leads` | Solicitudes de /para-tiendas y /para-restaurantes |
| `newsletter_subscribers` | Lista general + lista blog |
| `reviews` | Reseñas de producto (integración Judge.me) |

### Convenciones de nomenclatura

> ⚠️ Por definir con el webmaster.

---

## API & Endpoints

### Endpoints críticos conocidos

#### Productos y catálogo
- `GET /products` — listado de productos para /tienda
- `GET /products/:slug` — ficha de producto individual

#### Carrito y checkout
- `POST /cart/add` — añadir producto al carrito
- `PUT /cart/update` — actualizar cantidad
- `DELETE /cart/remove` — eliminar línea de carrito
- `POST /checkout/session` — crear sesión de Stripe Checkout
- `POST /webhooks/stripe` — webhook de Stripe para confirmar el pago y disparar post-compra

#### Autenticación
- `POST /auth/register` — registro con email + contraseña (incentivo: cuchara CremaCuadrado)
- `POST /auth/login` — login con email + contraseña
- `GET /auth/google` — inicio de flujo Google OAuth
- `GET /auth/google/callback` — callback de Google OAuth
- `POST /auth/guest` — compra como invitado (solo email)

#### Suscripción mensual (club mensual)
- `POST /subscriptions` — crear suscripción recurrente −15%
- `DELETE /subscriptions/:id` — cancelar suscripción
- `GET /subscriptions/me` — estado de suscripción del usuario

#### Formularios B2B
- `POST /leads/punto-de-venta` — formulario /para-tiendas → guarda lead con etapa "Solicitud punto de venta recibida" + email confirmación al solicitante + notificación a b2b@cremacuadrado.com
- `POST /leads/profesional` — formulario /para-restaurantes → guarda lead con etapa "Solicitud profesional recibida" + email confirmación al solicitante + notificación a b2b@cremacuadrado.com
- `POST /leads/puntos-de-venta-interes` — formulario captación /puntos-de-venta → guarda lead con etapa "Interés punto de venta"

#### Email marketing
- `POST /newsletter/subscribe` — captura general (homepage bloque 5) → lista "General"
- `POST /newsletter/blog` — captura blog → lista "Suscriptores blog"
- `POST /newsletter/pistagreta` — lista de espera Pistagreta → lista "Pistagreta"

#### Reseñas
- `POST /webhooks/judgedotme` — webhook de Judge.me al recibir nueva reseña

### Autenticación y autorización

- **Google OAuth** para login rápido (un clic, datos autorellenados)
- **JWT** para sesiones autenticadas
> ⚠️ Librería y configuración exacta por definir

### Formato estándar de respuestas

> ⚠️ Por definir. Recomendación: `{ success: true, data: {}, error: null }` / `{ success: false, data: null, error: { code, message } }`

---

## Modelos de Datos Clave

### Productos y variantes

```
Product
  id, slug, name, description, category (pura | crunchy | pistagreta)
  active (boolean)
  created_at, updated_at

ProductVariant
  id, product_id (FK)
  format (100g | 200g | 1kg)
  price (en céntimos — nunca floats para dinero)
  price_per_100g (calculado)
  stock_quantity
  sku
```

### Precios vigentes (hardcoded en DB)

| Formato | Precio | €/100g |
|---|---|---|
| 100g | 9,90€ | 9,90€ |
| 200g | 15,00€ | 7,50€ |
| 1kg | 55,00€ | 5,50€ |
| Profesionales | 44,00€/kg (mínimo 3kg) | — |

### Usuarios

```
User
  id, email, name, password_hash (null si Google OAuth)
  google_id (null si registro manual)
  role (customer | admin)
  has_received_spoon (boolean) — cuchara gratis con primer pedido al registrarse
  created_at

Address
  id, user_id (FK)
  name, surname, street, city, province, postal_code, phone
  is_default (boolean)
```

### Pedidos

```
Order
  id, user_id (FK, null si invitado)
  guest_email (null si usuario registrado)
  status (pending | paid | processing | shipped | delivered | cancelled)
  stripe_payment_intent_id
  shipping_address (JSON)
  billing_address (JSON)
  billing_same_as_shipping (boolean)
  nif_cif (null si no quiere factura)
  subtotal, shipping_cost, discount_amount, total (todos en céntimos)
  discount_code_id (FK, null)
  city (para variable dinámica en email post-compra "Tu pistacho llegó hasta [ciudad]")
  include_spoon (boolean) — si el usuario se registró por primera vez en esta compra
  created_at

OrderItem
  id, order_id (FK), product_variant_id (FK)
  quantity, unit_price, subtotal
```

### Suscripciones (club mensual)

```
Subscription
  id, user_id (FK)
  product_variant_id (FK) — formato 200g por defecto
  status (active | cancelled | paused)
  discount_percent (15)
  next_billing_date
  stripe_subscription_id
  created_at, cancelled_at
```

### Descuentos

```
DiscountCode
  id, code (unique)
  type (percentage | fixed)
  value (ej: 10 para 10%)
  valid_from, valid_until
  max_uses, current_uses
  single_use_per_user (boolean)
  active (boolean)
```

### Leads B2B

```
B2BLead
  id, type (punto_de_venta | profesional | interes_punto_de_venta)
  name, establishment_name, city, email, phone
  establishment_type (dropdown value)
  status (new | contacted | sample_sent | closed_won | closed_lost)
  created_at
```

---

## Reglas de Negocio

### Precios y descuentos

- El club mensual aplica **−15% permanente** sobre el precio base del formato 200g
- Los códigos de descuento se validan antes de crear la sesión de Stripe
- Si el usuario se registra en el checkout recibe una cuchara CremaCuadrado — se marca `has_received_spoon = true` en el usuario y `include_spoon = true` en el pedido. Solo una vez por usuario.

### Envíos

| Destino | Coste | Gratis a partir de |
|---|---|---|
| Península | 6,00€ | 48,00€ |
| Baleares | 8,50€ | No aplica gratis |

- El coste de envío se calcula en el backend, nunca en el frontend
- Los pedidos del club mensual tienen envío siempre gratuito

### Stock

- Mostrar aviso "Solo quedan X unidades" cuando `stock_quantity < 10`
- El stock se descuenta al confirmar el pago (webhook de Stripe), no al añadir al carrito

### Checkout

- Tres opciones de identificación: Google OAuth / Registro con cuenta / Compra como invitado
- Los datos de facturación son opcionales: si `billing_same_as_shipping = true` se copia la dirección de envío
- Si el usuario quiere factura debe proporcionar NIF/CIF

### Post-compra (triggers al confirmar pago)

El webhook `POST /webhooks/stripe` al recibir `payment_intent.succeeded` debe:
1. Actualizar `order.status` a `paid`
2. Descontar stock de cada variante
3. Marcar código de descuento como usado (si aplica)
4. Disparar secuencia de email post-compra en la herramienta de email marketing
5. Si `include_spoon = true`: añadir nota interna para incluir cuchara en el pedido
6. Si `user_id` existe: actualizar historial de pedidos del usuario
7. Registrar evento `purchase` en GA4 server-side si aplica

### Suscripción mensual

- Al cancelar: `status = cancelled`, `cancelled_at = now()`. No se cobra más.
- El descuento del 15% se aplica siempre que `subscription.status = active`
- Los pedidos de suscripción tienen envío gratuito independientemente del importe

---

## Integraciones Externas

### Stripe

- **Propósito**: procesador de pagos principal
- **Métodos aceptados**: tarjeta (Visa, Mastercard). Sin Bizum en el lanzamiento.
- **Flujo**: el backend crea una `checkout.session` o `payment_intent`, el frontend redirige o embebe el formulario de Stripe, Stripe confirma vía webhook
- **Webhook endpoint**: `POST /webhooks/stripe` — verificar firma con `STRIPE_WEBHOOK_SECRET`
- **Variables de entorno**: `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PUBLISHABLE_KEY`

### Leads B2B (tabla propia, sin CRM externo)

- **Propósito**: gestión del pipeline B2B
- **Etapas configuradas**:
  - "Solicitud punto de venta recibida" — formulario /para-tiendas
  - "Solicitud profesional recibida" — formulario /para-restaurantes
  - "Interés punto de venta" — formulario /puntos-de-venta
- **Trigger**: al enviar cualquier formulario B2B, se guarda el lead en la tabla `B2BLead` + email de confirmación al solicitante + notificación interna a b2b@cremacuadrado.com (Lucas y Stefano)
- Integración con un CRM externo (ej. HubSpot) queda como posible mejora futura, no implementada por ahora.

### Google OAuth

- **Propósito**: login rápido en el checkout (un clic, datos autorellenados)
- **Variables de entorno**: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_CALLBACK_URL`

### Judge.me

- **Propósito**: sistema de reseñas verificadas sin login del cliente
- **Flujo**: Judge.me envía email automático 3 días después de la entrega estimada con enlace directo al formulario de reseña
- **Variables de entorno**: `JUDGEDOTME_API_KEY`

### Email marketing

> ⚠️ Herramienta por definir (Klaviyo o ActiveCampaign recomendado)

**Listas a configurar**:
- "General" — captura homepage
- "Suscriptores blog" — captura /el-archivo
- "Pistagreta" — lista de espera /pistagreta

**Secuencias a configurar**:
1. **Bienvenida B2C** — al suscribirse sin haber comprado
2. **Post-compra B2C** — al confirmar pago: email inmediato + día 2 (recetas) + día 3 (solicitud reseña)
3. **Carrito abandonado B2C** — +1h / +24h / +48h / +72h
4. **Post-muestra B2B punto de venta** — al enviar formulario /para-tiendas
5. **Post-solicitud B2B profesional** — al enviar formulario /para-restaurantes

### Google Analytics 4

- **Eventos obligatorios a implementar**:
  - `view_item` — al cargar ficha de producto
  - `select_item` — al cambiar formato en el selector
  - `add_to_cart` — al hacer clic en "Añadir al carrito"
  - `begin_checkout` — al ir al pago
  - `purchase` — al confirmar pago (desde webhook Stripe)
  - `scroll_25/50/75` — en homepage
  - `email_capture` — al suscribirse al newsletter

### Meta Pixel

- **Eventos obligatorios**: `AddToCart`, `InitiateCheckout`, `Purchase`
- **Variables de entorno**: `META_PIXEL_ID`

### Google Maps

- **Propósito**: mapa interactivo en /puntos-de-venta
- **Variables de entorno**: `GOOGLE_MAPS_API_KEY`

---

## Convenciones de Código

> ⚠️ Por definir con el webmaster. Recomendaciones:

- **Dinero**: siempre en céntimos (enteros). Nunca usar `float` para precios.
- **Fechas**: UTC en base de datos, formatear en el cliente según locale
- **Variables de entorno**: todas las credenciales en `.env`, nunca hardcodeadas
- **Logs**: registrar todos los eventos de pago y webhooks de Stripe
- **Errores de pago**: no exponer detalles internos al cliente. Mensaje genérico + log interno.

---

## Comandos Esenciales

> ⚠️ Por definir según stack elegido.

---

## Lo que NUNCA se debe hacer

### Precios y dinero
- ❌ Nunca usar `float` para almacenar precios. Usar céntimos (enteros).
- ❌ Nunca calcular el coste de envío en el frontend. Siempre validar en el backend.
- ❌ Nunca confiar en el precio enviado desde el cliente. Siempre recalcular en el servidor antes de crear la sesión de Stripe.

### Seguridad
- ❌ Nunca exponer `STRIPE_SECRET_KEY` en el frontend.
- ❌ Nunca procesar un webhook de Stripe sin verificar la firma (`stripe-signature` header).
- ❌ Nunca almacenar datos de tarjeta. Stripe lo gestiona.

### Negocio
- ❌ No decir "pistacho manchego certificado" — el origen es principalmente manchego pero no está certificado oficialmente.
- ❌ No decir "hecha a mano" — se usa maquinaria (molino eléctrico, repeladora mecánica).
- ❌ No prometer "consistencia lote a lote" — es un producto artesanal con variaciones naturales.
- ❌ No mostrar el precio mayorista B2B en páginas públicas — se comunica por teléfono.

### Stock
- ❌ Nunca descontar stock al añadir al carrito. Solo al confirmar el pago.

### Suscripciones
- ❌ No cancelar suscripciones sin confirmación explícita del usuario.
- ❌ No cobrar el envío en pedidos del club mensual.
