#!/usr/bin/env python3
"""
Baja personajes de manhwas (Corea), manhuas (China) y webtoons desde la API pública de AniList y los deja en el formato de !rw:
    Nombre / Imagen / Fuente / Valor / Genero

AniList trae GÉNERO real, FAVORITOS reales (para el Valor) y la serie de cada personaje, así que no hace falta adivinar nada.
No necesita instalar nada (solo Python 3.8+) ni API key. Tiene que correrse en TU computadora (el servidor donde yo trabajo no llega a internet).

Uso básico (30 % hombres / 70 % mujeres, suma a tu rw actual):
    python3 obtener_manhwas.py --rw rw.json --salida carpeta_salida

Opciones útiles:
    --paises KR CN        KR = manhwa/webtoon coreano, CN = manhua chino, TW = Taiwán, JP = manga japonés (por si querés probar)
    --obras 1500          cuántas obras (las más populares) bajar POR país. Más obras = más personajes y más tiempo
    --personajes 25       cuántos personajes (los más queridos) bajar por obra (25 es el máximo de una página)
    --mujeres 70          porcentaje de mujeres en lo que se agrega (el resto, hombres)
    --pausa 1.0           segundos entre pedidos (AniList permite ~90 por minuto; si te tira 429 el script espera solo)
    --incluir-adulto      por defecto se EXCLUYEN las obras +18 (es un bot de Discord). Con esta opción se incluyen.

El script guarda lo que va bajando en `anilist_cache.json` dentro de la carpeta de salida: si se corta (internet, Ctrl+C),
volvé a correrlo igual y sigue donde quedó. Para empezar de cero, borrá ese archivo.

Genera en la carpeta de salida:
    rw_manhwas_nuevos.json   -> solo lo agregado
    rw_actualizado.json      -> tu rw + lo agregado (si pasaste --rw)
"""
import argparse, json, math, re, sys, time, unicodedata, urllib.request, urllib.error
from pathlib import Path

API = "https://graphql.anilist.co"

Q_OBRAS = """
query ($page: Int, $pais: CountryCode, $adulto: Boolean) {
  Page(page: $page, perPage: 50) {
    pageInfo { hasNextPage }
    media(type: MANGA, countryOfOrigin: $pais, isAdult: $adulto, format_in: [MANGA, ONE_SHOT], sort: FAVOURITES_DESC) {
      id favourites title { romaji english }
    }
  }
}"""
Q_OBRAS_TODAS = Q_OBRAS.replace("isAdult: $adulto, ", "").replace(", $adulto: Boolean", "")  # con --incluir-adulto

Q_PERSONAJES = """
query ($id: Int, $page: Int, $por: Int) {
  Media(id: $id) {
    characters(page: $page, perPage: $por, sort: FAVOURITES_DESC) {
      pageInfo { hasNextPage }
      edges { role node { id gender favourites name { full } image { large } } }
    }
  }
}"""


def graphql(query, variables, intentos=6):
    cuerpo = json.dumps({"query": query, "variables": variables}).encode()
    for n in range(intentos):
        req = urllib.request.Request(API, cuerpo, {"Content-Type": "application/json", "Accept": "application/json",
                                                   "User-Agent": "rw-bot-importer/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                espera = int(e.headers.get("Retry-After", "60")) + 2
                print(f"  (AniList pide esperar {espera}s)", flush=True); time.sleep(espera); continue
            if e.code >= 500: time.sleep(5 * (n + 1)); continue
            raise
        except (urllib.error.URLError, TimeoutError):
            time.sleep(5 * (n + 1))
    raise RuntimeError("AniList no respondió después de varios intentos")


def ascii_(s): return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
def clave(s):
    t = ascii_(s); t = re.sub(r"ou|oh|uh", lambda m: m.group(0)[0], t); t = re.sub(r"([aeiou])\1+", r"\1", t)
    return "".join(sorted(re.findall(r"[a-z0-9]+", t)))

CURVA = [5200, 10000, 14500, 15000, 18000, 20000, 25000, 35000, 45000, 55000, 61000, 75000, 95000,
         120000, 150000, 180000, 200000, 300000, 400000, 500000, 2500000]   # percentiles de 5 en 5 % de tu rw_16
def valor_en(q):
    x = q * (len(CURVA) - 1); i = min(int(x), len(CURVA) - 2); return CURVA[i] + (x - i) * (CURVA[i + 1] - CURVA[i])
def redondeo(v):
    return int(round(v / 1_000) * 1_000) if v < 100_000 else int(round(v / 5_000) * 5_000) if v < 500_000 else int(round(v / 50_000) * 50_000)


def bajar(args, out_dir):
    cache_path = out_dir / "anilist_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {"obras": {}, "personajes": {}}
    guardar = lambda: cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    q_obras = Q_OBRAS_TODAS if args.incluir_adulto else Q_OBRAS

    for pais in args.paises:
        if pais in cache["obras"] and len(cache["obras"][pais]) >= args.obras:
            continue
        lista, pagina = [], 1
        while len(lista) < args.obras:
            vars_ = {"page": pagina, "pais": pais, **({} if args.incluir_adulto else {"adulto": False})}
            d = graphql(q_obras, vars_)
            if "errors" in d: raise RuntimeError(d["errors"])
            pg = d["data"]["Page"]; lista += pg["media"]; pagina += 1
            print(f"[{pais}] obras: {len(lista)}", flush=True); time.sleep(args.pausa)
            if not pg["pageInfo"]["hasNextPage"]: break
        cache["obras"][pais] = lista[:args.obras]; guardar()

    pendientes = [(p, o) for p in args.paises for o in cache["obras"].get(p, []) if str(o["id"]) not in cache["personajes"]]
    for i, (pais, o) in enumerate(pendientes, 1):
        d = graphql(Q_PERSONAJES, {"id": o["id"], "page": 1, "por": min(args.personajes, 25)})
        if "errors" in d:
            print("  salteo obra", o["id"], d["errors"][0].get("message")); cache["personajes"][str(o["id"])] = []; continue
        edges = d["data"]["Media"]["characters"]["edges"]
        cache["personajes"][str(o["id"])] = edges
        titulo = o["title"]["english"] or o["title"]["romaji"]
        print(f"[{i}/{len(pendientes)}] {titulo[:50]} ({len(edges)} personajes)", flush=True)
        if i % 25 == 0: guardar()
        time.sleep(args.pausa)
    guardar()
    return cache


def armar(cache, args, existentes):
    ya = {clave(x["Nombre"]) for x in existentes}
    ya_serie = {(clave(x["Nombre"]), clave(x["Fuente"])) for x in existentes}
    mejores, desc = {}, {"sin género (o no binario)": 0, "imagen por defecto": 0, "ya estaba en tu rw": 0}
    for pais, obras in cache["obras"].items():
        for o in obras:
            titulo = (o["title"]["english"] or o["title"]["romaji"] or "").strip()
            for e in cache["personajes"].get(str(o["id"]), []):
                n = e["node"]; nombre = (n["name"]["full"] or "").strip()
                gen = {"female": "F", "male": "M"}.get((n.get("gender") or "").lower())
                img = ((n.get("image") or {}).get("large") or "").strip()
                if not nombre or len(nombre) < 2:  continue
                if not gen:                         desc["sin género (o no binario)"] += 1; continue
                if not img or "default" in img.rsplit("/", 1)[-1].lower(): desc["imagen por defecto"] += 1; continue
                k = clave(nombre)
                if (k in ya and len(nombre.split()) >= 2) or (k, clave(titulo)) in ya_serie: desc["ya estaba en tu rw"] += 1; continue
                # puntaje: favoritos del personaje (peso grande) + popularidad de la obra; los protagonistas suman un poco
                score = math.log1p(n.get("favourites") or 0) + 0.35 * math.log1p(o.get("favourites") or 0) + (0.5 if e.get("role") == "MAIN" else 0)
                clave_p = (n["id"])
                if clave_p not in mejores or score > mejores[clave_p][0]:
                    mejores[clave_p] = (score, nombre, img, titulo, gen)
    F = sorted((v for v in mejores.values() if v[4] == "F"), key=lambda t: t[0], reverse=True)
    M = sorted((v for v in mejores.values() if v[4] == "M"), key=lambda t: t[0], reverse=True)
    pf = args.mujeres / 100.0
    if len(F) / max(len(F) + len(M), 1) >= pf: nF, nM = min(len(F), round(len(M) * pf / (1 - pf))), len(M)
    else:                                       nF, nM = len(F), min(len(M), round(len(F) * (1 - pf) / pf))
    elegidos = F[:nF] + M[:nM]
    elegidos.sort(key=lambda t: t[0])
    nuevos = []
    for i, (_, nombre, img, titulo, gen) in enumerate(elegidos):
        q = i / max(len(elegidos) - 1, 1)
        nuevos.append({"Nombre": nombre, "Imagen": img, "Fuente": titulo, "Valor": redondeo(valor_en(q * 0.95)), "Genero": gen})
    nuevos.sort(key=lambda x: -x["Valor"])
    return nuevos, desc, (len(F), len(M))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--salida", required=True); ap.add_argument("--rw")
    ap.add_argument("--paises", nargs="+", default=["KR", "CN"]); ap.add_argument("--obras", type=int, default=1500)
    ap.add_argument("--personajes", type=int, default=25); ap.add_argument("--mujeres", type=float, default=70)
    ap.add_argument("--pausa", type=float, default=1.0); ap.add_argument("--incluir-adulto", action="store_true")
    args = ap.parse_args()
    out_dir = Path(args.salida); out_dir.mkdir(parents=True, exist_ok=True)
    existentes = json.loads(Path(args.rw).read_text(encoding="utf-8")) if args.rw else []

    cache = bajar(args, out_dir)
    nuevos, desc, (nf, nm) = armar(cache, args, existentes)

    dump = lambda p, o: p.write_text("[\n" + ",\n".join(json.dumps(x, ensure_ascii=False) for x in o) + "\n]", encoding="utf-8")
    dump(out_dir / "rw_manhwas_nuevos.json", nuevos)
    if existentes: dump(out_dir / "rw_actualizado.json", sorted(existentes + nuevos, key=lambda x: -x["Valor"]))
    f = sum(1 for x in nuevos if x["Genero"] == "F")
    print(f"\nDisponibles: {nf} mujeres y {nm} hombres | descartados: {desc}")
    print(f"Agregados: {len(nuevos)} ({f} mujeres = {f / max(len(nuevos), 1):.0%}, {len(nuevos) - f} hombres)")


if __name__ == "__main__":
    main()
