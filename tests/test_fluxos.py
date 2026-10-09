# -*- coding: utf-8 -*-
"""
Testes de ponta a ponta dos fluxos do CRM (login, perfis, SLA, distribuição, relatórios...).
Rodar a partir da pasta do projeto:   python tests/test_fluxos.py
Usa um banco temporário; não toca no mba_crm.db.
"""
import os, re, sys, random, tempfile
os.environ["MBA_DB"] = os.path.join(tempfile.mkdtemp(), "teste_crm.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as M

def gera_cpf():
    n = [random.randint(0, 9) for _ in range(9)]
    for k in (9, 10):
        s = sum(n[i] * ((k + 1) - i) for i in range(k))
        n.append((s * 10) % 11 % 10)
    return "".join(map(str, n))

app = M.app
app.testing = True

def tok(c):
    with c.session_transaction() as s:
        return s.setdefault("_csrf", "t" * 32)

def post(c, url, data, **kw):
    data = dict(data); data["_csrf"] = tok(c)
    return c.post(url, data=data, **kw)

ok = lambda cond, msg: print(("OK  " if cond else "FALHA ") + msg) or (cond or sys.exit(1))

cpf_dir, cpf_ger, cpf_sup, cpf_a1, cpf_a2 = (gera_cpf() for _ in range(5))

c = app.test_client()
r = c.get("/"); ok(r.status_code == 302 and "/setup" in r.location, "sem usuários → setup")
r = post(c, "/setup", {"nome": "Diretor Teste", "cpf": cpf_dir}); ok("/login" in r.location, "diretor criado")
r = post(c, "/login", {"cpf": cpf_dir, "senha": "errada"}); ok("incorretos" in r.get_data(True), "senha errada rejeitada")
r = post(c, "/login", {"cpf": M.fmt_cpf(cpf_dir), "senha": "abc123"}); ok("/trocar-senha" in r.location, "1º login → troca obrigatória")
r = c.get("/analises"); ok("/trocar-senha" in r.location, "bloqueia navegação até trocar")
r = post(c, "/trocar-senha", {"atual": "abc123", "nova": "abc123", "confirma": "abc123"}); ok("diferente" in r.get_data(True) or "8 caracteres" in r.get_data(True), "não aceita manter abc123")
r = post(c, "/trocar-senha", {"atual": "abc123", "nova": "Diretor2026", "confirma": "Diretor2026"}); ok(r.status_code == 302, "senha trocada")
r = c.get("/"); ok("/relatorios" in r.location, "diretor cai em relatórios")

for nome, cpf, perfil in [("Gerente Teste", cpf_ger, "gerente"), ("Supervisora Teste", cpf_sup, "supervisor"),
                          ("Ana Analista", cpf_a1, "analista"), ("Bruno Analista", cpf_a2, "analista")]:
    r = post(c, "/usuarios/novo", {"nome": nome, "cpf": cpf, "perfil": perfil}); ok(r.status_code == 302, f"cria {perfil}")
r = post(c, "/usuarios/novo", {"nome": "X", "cpf": "12345678900", "perfil": "analista"}); ok("CPF inválido" in r.get_data(True), "CPF inválido rejeitado")

def entra(cpf, nova):
    cl = app.test_client()
    post(cl, "/login", {"cpf": cpf, "senha": "abc123"})
    post(cl, "/trocar-senha", {"atual": "abc123", "nova": nova, "confirma": nova})
    return cl

a1 = entra(cpf_a1, "Analista111"); a2 = entra(cpf_a2, "Analista222")
sup = entra(cpf_sup, "Superv1234"); ger = entra(cpf_ger, "Gerente123")

base = {"emissao": "2026-09-10", "status": "Aprovado - Condicional", "imovel_construtora": "Construtora Alfa",
        "imovel_empreendimento": "Residencial Sol", "imovel_avaliacao": "400.000,00",
        "c_nome": ["Joao Comprador", "Maria Compradora"], "c_cpf": ["067.003.795-80", ""], "c_nasc": ["1993-11-21", ""],
        "c_formal": ["0,00", "3.732,86"], "c_informal": ["2.550,00", "0"], "faixa": "HIS2",
        "fin_financiamento": "251.955,56", "fin_fgts": "0", "fin_subsidio": "0", "fin_proprios": "0",
        "fin_parcela": "1.833,86", "fin_vencimento": "2026-10-05", "fin_prazo": "420", "fin_amortizacao": "PRICE",
        "fin_enquadramento": "MCMV", "fin_taxa": "7,93% a.a.", "fin_meses36": "Sim", "fin_dependente": "Não",
        "comp_serasa": "Nada consta", "comp_cadmut": "Nada consta", "comp_ciweb": "Off", "comp_subsidio": "Nada consta",
        "obs": "Condicionado à apresentação do IR."}
ok(post(a1, "/analises/nova", base).status_code == 403, "analista não cria análise avulsa")
con = M.sqlite3.connect(os.environ["MBA_DB"])
ID = {cpf: i for i, cpf in con.execute("select id,cpf from usuarios")}
ID = {k: v for v, k in con.execute("select id,cpf from usuarios")}
r = post(sup, "/analises/nova", dict(base, analista_id=ID[cpf_a1])); ok(r.status_code == 302, "supervisor cria análise para analista")
aid = int(re.search(r"/analises/(\d+)", r.location).group(1))
html = a1.get(f"/analises/{aid}").get_data(True)
ok("R$ 6.282,86" in html and "R$ 251.955,56" in html and "APROVADO - CONDICIONAL" in html, "relatório com totais corretos")
b2 = dict(base, status="Reprovado", motivo_reprova="Dívidas vencidas", imovel_construtora="Construtora Beta", fin_financiamento="180.000,00", emissao="2026-08-15")
post(sup, "/analises/nova", dict(b2, analista_id=ID[cpf_a2]))
post(sup, "/analises/nova", dict(base, status="Pendente documentação", emissao="2026-09-20", analista_id=ID[cpf_a2]))

ok(a2.get(f"/analises/{aid}").status_code == 403, "analista não vê análise de outro")
ok(str(aid) not in re.findall(r"/analises/(\d+)'", a2.get("/analises").get_data(True)), "lista do analista só tem as dele")
ok(a1.get("/painel").status_code == 403 and a1.get("/relatorios").status_code == 403 and a1.get("/usuarios").status_code == 403, "analista sem painel/relatórios/usuários")
ok(post(a1, f"/analises/{aid}/excluir", {}).status_code == 403, "analista não exclui")
ok(sup.get("/painel").status_code == 200 and sup.get(f"/analises/{aid}").status_code == 200, "supervisor vê painel e todas as análises")
ok(sup.get("/relatorios").status_code == 403, "supervisor sem relatórios da diretoria")
ok(ger.get("/painel").status_code == 200 and ger.get("/relatorios").status_code == 403, "gerente: painel sim, relatórios não")
r = post(sup, "/usuarios/novo", {"nome": "Z", "cpf": gera_cpf(), "perfil": "gerente"}); ok("não permitido" in r.get_data(True), "supervisor não cria gerente")
r = post(ger, "/usuarios/novo", {"nome": "Sup2", "cpf": gera_cpf(), "perfil": "supervisor"}); ok(r.status_code == 302, "gerente cria supervisor")

# edição com mudança de status gera histórico
post(sup, f"/analises/{aid}/editar", dict(base, status="Aprovado"))
ok("Status: “Aprovado - Condicional” → “Aprovado”" in sup.get(f"/analises/{aid}").get_data(True), "histórico registra mudança de status")

p = sup.get("/painel?de=2026-08-01&ate=2026-09-30").get_data(True)
ok("Ana Analista" in p and "Bruno Analista" in p and "SICAQ" in p, "painel mostra equipe e alertas")
rel = c.get("/relatorios?de=2026-01-01&ate=2026-09-30").get_data(True)
ok("Taxa de aprovação" in rel and "Construtora Alfa" in rel and "Construtora Beta" in rel, "relatórios da diretoria")
ok("text/csv" in c.get("/relatorios/exportar.csv").content_type, "exporta CSV")
ok(c.get("/backup").status_code == 200, "backup do banco")

# reset de senha e desativação
uid_a1 = M.sqlite3.connect(os.environ["MBA_DB"]).execute("SELECT id FROM usuarios WHERE cpf=?", (cpf_a1,)).fetchone()[0]
post(sup, f"/usuarios/{uid_a1}/resetar", {})
cl = app.test_client(); r = post(cl, "/login", {"cpf": cpf_a1, "senha": "abc123"}); ok("/trocar-senha" in r.location, "reset volta para abc123 com troca obrigatória")
post(sup, f"/usuarios/{uid_a1}/ativo", {})
r = post(app.test_client(), "/login", {"cpf": cpf_a1, "senha": "abc123"}); ok("desativado" in r.get_data(True), "usuário desativado não entra")

# bloqueio após 5 erros
cl = app.test_client()
for _ in range(5): r = post(cl, "/login", {"cpf": cpf_a2, "senha": "x"})
r = post(cl, "/login", {"cpf": cpf_a2, "senha": "Analista222"}); ok("bloqueado" in r.get_data(True), "bloqueio após 5 tentativas")

# CSRF
ok(a2.post("/analises/nova", data={"status": "Aprovado"}).status_code == 400, "POST sem token CSRF é rejeitado")

# ================= SLA / distribuição =================
from datetime import datetime, timedelta
fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M")
agora_ = datetime.now()
def distribui(prot, minutos_atras, tipo, analista):
    return post(sup, "/distribuir", {"protocolo": prot, "solicitado_em": fmt(agora_ - timedelta(minutes=minutos_atras)),
                                     "tipo": tipo, "analista_id": ID[analista], "cliente": "Cliente " + prot})
ok(distribui("AP-1001", 10, "LEAD", cpf_a2).status_code == 302, "supervisor distribui protocolo LEAD")
distribui("AP-1002", 50, "LEAD", cpf_a2)      # 10 min restantes -> amarelo
distribui("AP-1003", 75, "LEAD", cpf_a2)      # atrasado -> vermelho
distribui("AP-1004", 5, "MESA", cpf_a2)       # 25 min -> azul
distribui("AP-1005", 22, "MESA", cpf_a2)      # 8 min -> amarelo
r = distribui("AP-1001", 1, "LEAD", cpf_a2); ok("ainda está em andamento" in r.get_data(True), "protocolo em aberto não é lançado de novo")
r = post(sup, "/distribuir", {"protocolo": "AP-9", "solicitado_em": fmt(agora_ + timedelta(hours=2)), "tipo": "LEAD", "analista_id": ID[cpf_a2]})
ok("futuro" in r.get_data(True), "horário no futuro bloqueado")
ok(post(a2, "/distribuir", {"protocolo": "X"}).status_code == 403, "analista não distribui")

h = a2.get("/fila").get_data(True)
ordem = [p for p in ["AP-1005", "AP-1004", "AP-1003", "AP-1002", "AP-1001"] if p in h]
pos = [h.index("Protocolo " + p) for p in ["AP-1005", "AP-1004", "AP-1003", "AP-1002", "AP-1001"]]
ok(pos == sorted(pos), "fila: MESA primeiro, depois por vencimento")
ok("2 protocolo(s) de MESA" in h, "alerta de MESA na fila do analista")
ok("distribuído por <b>Supervisora Teste</b>" in h, "analista vê quem distribuiu")
def estado(prot):
    r = con.execute("select * from analises where protocolo=?", (prot,)).fetchone()
    cols = [c[1] for c in con.execute("pragma table_info(analises)")]
    return M.sla(dict(zip(cols, r)))
ok(estado("AP-1001")["cor"] == "azul", "LEAD com 50 min restantes = azul")
ok(estado("AP-1002")["cor"] == "amarelo", "LEAD com 10 min restantes = amarelo")
ok(estado("AP-1003")["cor"] == "vermelho", "LEAD com 75 min = vermelho")
ok(estado("AP-1004")["cor"] == "azul", "MESA com 25 min restantes = azul")
ok(estado("AP-1005")["cor"] == "amarelo", "MESA com 8 min restantes = amarelo")

aid1 = con.execute("select id from analises where protocolo='AP-1001'").fetchone()[0]
post(sup, f"/analises/{aid1}/mesa", {})
con.commit()
ok(estado("AP-1001")["tipo"] == "MESA" and estado("AP-1001")["cor"] == "azul", "supervisor marca como MESA (prazo passa a 30 min)")
aid3 = con.execute("select id from analises where protocolo='AP-1003'").fetchone()[0]
aid2 = con.execute("select id from analises where protocolo='AP-1002'").fetchone()[0]
post(sup, f"/analises/{aid2}/transferir", {"analista_id": ID[cpf_sup]})
ok(a2.get(f"/analises/{aid2}").status_code == 403, "após transferência o analista antigo perde acesso")
hist = sup.get(f"/analises/{aid2}").get_data(True)
ok("Transferido de Bruno Analista para Supervisora Teste por Supervisora Teste" in hist, "histórico de transferência")
ok("distribuído por Supervisora Teste para Bruno Analista" in hist, "histórico de distribuição")
# analista registra parecer -> encerra relógio
# ---- iniciar / finalizar / trava ----
r = a2.get(f"/analises/{aid3}/editar"); ok(r.status_code == 302 and "/fila" in r.location, "analista precisa clicar em Iniciar antes de editar")
r = post(a2, f"/analises/{aid3}/iniciar", {}); ok("/editar" in r.location, "analista inicia LEAD")
distribui("AP-1006", 1, "LEAD", cpf_a2)
aid6 = con.execute("select id from analises where protocolo='AP-1006'").fetchone()[0]
r = post(a2, f"/analises/{aid6}/iniciar", {}); ok("/fila" in r.location, "bloqueia iniciar outro LEAD com um em andamento")
ok("já está com o protocolo AP-1003" in a2.get("/fila").get_data(True), "mensagem de bloqueio mostra o protocolo atual")
aid4 = con.execute("select id from analises where protocolo='AP-1004'").fetchone()[0]
r = post(a2, f"/analises/{aid4}/iniciar", {}); ok("/editar" in r.location, "MESA pode ser iniciada mesmo com outro em andamento")
r = post(a2, f"/analises/{aid3}/editar", dict(base, status="Reprovado", acao="finalizar"))
ok("Informe o motivo da reprova" in r.get_data(True), "reprova exige motivo")
r = post(a2, f"/analises/{aid3}/editar", dict(base, status="Em análise", acao="finalizar"))
ok("escolha o parecer" in r.get_data(True), "finalizar exige parecer")
post(a2, f"/analises/{aid3}/editar", dict(base, status="Reprovado", motivo_reprova="Compromissos financeiros", acao="salvar"))
con2 = M.sqlite3.connect(os.environ["MBA_DB"])
ok(con2.execute("select concluido_em from analises where id=?", (aid3,)).fetchone()[0] is None, "salvar não finaliza (tempo continua)")
post(a2, f"/analises/{aid3}/editar", dict(base, status="Reprovado", motivo_reprova="Compromissos financeiros", acao="finalizar"))
row = con2.execute("select concluido_em, iniciado_em, motivo_reprova from analises where id=?", (aid3,)).fetchone()
ok(row[0] is not None and row[1] is not None and row[2] == "Compromissos financeiros", "FINALIZAR para SLA e tempo de análise e grava motivo")
h3 = a2.get(f"/analises/{aid3}").get_data(True)
ok("Concluído com atraso" in h3 and "tempo de análise" in h3 and "MOTIVO: COMPROMISSOS FINANCEIROS" in h3, "relatório mostra SLA, tempo e motivo")
ok("Protocolo AP-1003" not in a2.get("/fila").get_data(True), "finalizado sai da fila")
r = post(a2, f"/analises/{aid6}/iniciar", {}); ok("/fila" in r.location, "ainda bloqueado enquanto a MESA está em andamento")
post(a2, f"/analises/{aid4}/editar", dict(base, status="Aprovado", acao="finalizar"))
r = post(a2, f"/analises/{aid6}/iniciar", {}); ok("/editar" in r.location, "após finalizar tudo, libera o próximo LEAD")

# ---- redistribuição ----
r = distribui("AP-1006", 1, "LEAD", cpf_sup); ok("em andamento" in r.get_data(True), "não redistribui protocolo em andamento")
r = distribui("AP-1003", 2, "LEAD", cpf_sup); h = r.get_data(True)
ok("já cadastrado" in h and "Confirmar redistribuição" in h and "Compromissos financeiros" in h, "protocolo já cadastrado puxa do histórico e pede confirmação")
r = post(sup, "/distribuir", {"protocolo": "AP-1003", "solicitado_em": fmt(agora_ - timedelta(minutes=2)), "tipo": "LEAD",
                              "analista_id": ID[cpf_sup], "redistribuir": str(aid3)})
ok(r.status_code == 302, "confirma redistribuição")
novo = M.sqlite3.connect(os.environ["MBA_DB"]).execute("select id, origem_id, status, compradores, concluido_em from analises where protocolo='AP-1003' order by id desc").fetchone()
ok(novo[1] == aid3 and novo[2] == "Em análise" and "Joao Comprador" in novo[3] and novo[4] is None, "redistribuição reaproveita os dados sem recadastrar")
j = sup.get("/distribuir/consulta?protocolo=AP-1003").get_json()
ok(j["existe"] and j["vezes"] == 2 and j["aberto"], "consulta de protocolo pelo campo")

p = sup.get("/painel").get_data(True)
ok("SLA dos protocolos" in p and "MESA abertas" in p, "painel com SLA")
rel = c.get("/relatorios").get_data(True)
ok("SLA dos protocolos" in rel and "Protocolos distribuídos por" in rel, "relatórios com SLA")
ok("AP-1003" in c.get("/relatorios/exportar.csv").get_data(True), "CSV com protocolo")
# edição não pode apagar protocolo pelo analista
post(a2, f"/analises/{aid3}/editar", dict(base, status="Reprovado", motivo_reprova="Dívidas vencidas", protocolo="HACK", tipo="LEAD"))
ok(M.sqlite3.connect(os.environ["MBA_DB"]).execute("select protocolo from analises where id=?", (aid3,)).fetchone()[0] == "AP-1003", "analista não altera protocolo")
# ---- relatórios v1.2 ----
rel = c.get("/relatorios?de=2026-01-01").get_data(True)
ok("Reprovados por motivo" in rel and "Rendas informais" in rel and "Rendas formais" in rel and "Ticket médio" not in rel and "Volume aprovado" not in rel, "relatório do diretor com novos indicadores")
rel = c.get("/relatorios?de=2026-01-01&motivo=D%C3%ADvidas+vencidas").get_data(True)
ok("Dívidas vencidas · 2 processo" in rel, "filtro por motivo de reprova")
p = sup.get("/painel").get_data(True)
ok("zera todo dia 1º" in p and "Tempo médio de análise" in p, "painel: volume do mês e tempo de análise")

# ---- excluir usuário ----
uid_b = ID[cpf_a2]
n_b = M.sqlite3.connect(os.environ["MBA_DB"]).execute("select count(*) from analises where analista_id=?", (uid_b,)).fetchone()[0]
ok(a2.get(f"/usuarios/{ID[cpf_sup]}/excluir").status_code == 403, "analista não exclui usuários")
r = post(sup, f"/usuarios/{uid_b}/excluir", {"confirma_cpf": "000", "destino_id": ID[cpf_sup]}); ok("digite o CPF" in r.get_data(True), "exclusão exige confirmar CPF")
r = post(sup, f"/usuarios/{uid_b}/excluir", {"confirma_cpf": cpf_a2}); ok("Escolha para quem" in r.get_data(True), "exclusão exige destino das análises")
r = post(sup, f"/usuarios/{uid_b}/excluir", {"confirma_cpf": cpf_a2, "destino_id": ID[cpf_sup]}); ok(r.status_code == 302, "usuário excluído")
cx = M.sqlite3.connect(os.environ["MBA_DB"])
ok(cx.execute("select count(*) from usuarios where id=?", (uid_b,)).fetchone()[0] == 0, "registro removido")
ok(cx.execute("select count(*) from analises where analista_id=?", (ID[cpf_sup],)).fetchone()[0] >= n_b, "análises passaram para o destino")
ok("Bruno Analista" in sup.get(f"/analises/{aid3}").get_data(True), "histórico preserva o nome")
r = post(app.test_client(), "/login", {"cpf": cpf_a2, "senha": "Analista222"}); ok("incorretos" in r.get_data(True), "usuário excluído não entra")

# ---- gerente edita diretor ----
cx = M.sqlite3.connect(os.environ["MBA_DB"])
uid_dir = cx.execute("select id from usuarios where perfil='diretor'").fetchone()[0]
ok(ger.get(f"/usuarios/{uid_dir}/editar").status_code == 200, "gerente abre edição do diretor")
ok(sup.get(f"/usuarios/{uid_dir}/editar").status_code == 403, "supervisor não edita diretor")
r = post(ger, f"/usuarios/{uid_dir}/editar", {"nome": "Diretor Editado", "cpf": cpf_dir, "email": "d@mba.com", "perfil": "diretor"})
ok(r.status_code == 302 and M.sqlite3.connect(os.environ["MBA_DB"]).execute("select nome, perfil from usuarios where id=?", (uid_dir,)).fetchone() == ("Diretor Editado", "diretor"), "gerente salva dados do diretor")
r = post(ger, f"/usuarios/{uid_dir}/editar", {"nome": "X", "cpf": cpf_dir, "perfil": "analista"})
ok("não permitido" in r.get_data(True), "gerente não rebaixa o perfil do diretor")
ok(post(ger, f"/usuarios/{uid_dir}/resetar", {}).status_code == 403 and post(ger, f"/usuarios/{uid_dir}/ativo", {}).status_code == 403
   and ger.get(f"/usuarios/{uid_dir}/excluir").status_code == 403, "gerente não reseta, desativa nem exclui o diretor")
print("\nTodos os testes passaram.")
