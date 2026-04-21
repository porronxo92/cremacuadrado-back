"""
Script para inicializar la base de datos de Cremacuadrado en Supabase.
Ejecutar: python setup_database.py

Asegúrate de configurar DATABASE_URL en el archivo .env
"""
import os
import sys
from urllib.parse import quote_plus

# Cargar .env
from pathlib import Path
env_path = Path(__file__).parent / '.env'
if env_path.exists():
    with open(env_path) as f:
        for line in f:
            if '=' in line and not line.startswith('#'):
                key, value = line.strip().split('=', 1)
                os.environ[key] = value

try:
    import psycopg2
except ImportError:
    print("Instalando psycopg2-binary...")
    os.system("pip install psycopg2-binary")
    import psycopg2

def get_connection():
    """Obtiene conexión a la base de datos"""
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        print("ERROR: DATABASE_URL no configurada en .env")
        sys.exit(1)
    
    try:
        conn = psycopg2.connect(database_url)
        conn.autocommit = False
        return conn
    except Exception as e:
        print(f"ERROR conectando a la base de datos: {e}")
        sys.exit(1)

def execute_sql_file(conn, filepath, description):
    """Ejecuta un archivo SQL"""
    print(f"\n{'='*50}")
    print(f"Ejecutando: {description}")
    print(f"Archivo: {filepath}")
    print('='*50)
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            sql = f.read()
        
        cursor = conn.cursor()
        cursor.execute(sql)
        conn.commit()
        print(f"✓ {description} ejecutado correctamente")
        return True
    except Exception as e:
        conn.rollback()
        print(f"✗ Error: {e}")
        return False

def main():
    print("\n" + "="*60)
    print("  CREMACUADRADO - Inicialización de Base de Datos")
    print("="*60)
    
    # Obtener conexión
    print("\nConectando a la base de datos...")
    conn = get_connection()
    print("✓ Conexión establecida")
    
    # Rutas de los scripts SQL
    base_path = Path(__file__).parent.parent / 'database' / 'scripts'
    
    scripts = [
        (base_path / '001_schema.sql', 'Creación del esquema (tablas, índices, triggers)'),
        (base_path / '002_seed_data.sql', 'Datos iniciales (categorías, productos, blog)'),
        (base_path / '003_seed_admin.sql', 'Usuario administrador y datos de prueba'),
    ]
    
    # Ejecutar scripts
    all_success = True
    for filepath, description in scripts:
        if filepath.exists():
            success = execute_sql_file(conn, filepath, description)
            if not success:
                all_success = False
                break
        else:
            print(f"\n⚠ Archivo no encontrado: {filepath}")
            all_success = False
    
    # Cerrar conexión
    conn.close()
    
    # Resumen
    print("\n" + "="*60)
    if all_success:
        print("  ✓ BASE DE DATOS INICIALIZADA CORRECTAMENTE")
        print("="*60)
        print("\nUsuarios de prueba creados:")
        print("  - Admin: admin@cremacuadrado.com / Admin123!")
        print("  - Cliente: cliente@test.com / Cliente123!")
        print("\nPuedes iniciar el backend con:")
        print("  cd backend && uvicorn app.main:app --reload")
    else:
        print("  ✗ HUBO ERRORES DURANTE LA INICIALIZACIÓN")
        print("="*60)
        print("\nRevisa los errores anteriores y vuelve a ejecutar el script.")
    
    print()

if __name__ == '__main__':
    main()
