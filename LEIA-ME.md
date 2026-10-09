# CRM de Análises de Crédito · Your Home

Sistema web para registrar as análises de crédito, gerar o relatório padrão em PDF e acompanhar a produção da equipe.
Feito em Python (Flask) com banco SQLite. Todos acessam pelo navegador.

## 1. Como rodar (Windows)

1. Instale o **Python 3.9 ou mais novo** (python.org). Na instalação, marque **"Add Python to PATH"**.
2. Descompacte esta pasta num computador que fique ligado durante o expediente (pode ser um PC da empresa).
3. Dê dois cliques em **`iniciar.bat`**. Na primeira vez ele instala o que precisa (1 a 2 minutos).
4. A janela mostra dois endereços:
   - `http://localhost:5000`: para usar no próprio computador
   - `http://192.168.x.x:5000`: para os outros computadores da rede da empresa
5. Não feche a janela preta enquanto o sistema estiver em uso.

> Linux/Mac: `pip install -r requirements.txt` e depois `python app.py`.

## 2. Primeiro acesso

1. Na primeira vez em que o sistema for aberto, ele pede o cadastro do **Diretor** (nome e CPF).
2. Entre com o **CPF** e a senha padrão **abc123**.
3. O sistema obriga a troca por uma senha pessoal: mínimo de 8 caracteres, com letras e números, diferente de abc123 e do CPF.
4. Em **Usuários**, cadastre gerentes, supervisores e analistas. Todos começam com **abc123** e trocam no primeiro login.

## 3. Perfis e permissões

| | Analista | Supervisor | Gerente | Diretor |
|---|:-:|:-:|:-:|:-:|
| Distribuir protocolos / marcar MESA | – | ✓ | ✓ | ✓ |
| Editar análises | Só as dele | Todas | Todas | Todas |
| Ver análises de outros | – | ✓ | ✓ | ✓ |
| Transferir análise para outro analista | – | ✓ | ✓ | ✓ |
| Excluir análise | – | ✓ | ✓ | ✓ |
| Painel da equipe | – | ✓ | ✓ | ✓ |
| Exportar CSV (Excel) | – | ✓ | ✓ | ✓ |
| Relatórios gerais | – | – | – | ✓ |
| Backup do banco | – | – | – | ✓ |
| Cadastrar, desativar e excluir usuários | – | Analistas | Analistas e supervisores | Todos |
| Editar dados cadastrais (nome, CPF, e-mail) | – | Analistas | Analistas, supervisores e **diretor** | Todos |

- **Esqueceu a senha?** O gestor clica em *Redefinir senha* em Usuários. A senha volta para abc123 e a troca é pedida de novo.
- **Segurança:** as senhas ficam criptografadas. Depois de 5 senhas erradas, o acesso fica bloqueado por 15 minutos, e a sessão expira após 9 horas.
- **Desligamento:** *Desativar* bloqueia o acesso e mantém tudo. *Excluir* apaga o login de vez: o sistema pede o CPF
  para confirmar e para quem vão as análises da pessoa; o histórico continua registrado com o nome dela.

## 4. Telas

- **Análises**: lista com busca por comprador, CPF, empreendimento ou construtora, com filtros por status, analista e período.
- **Relatório da análise**: layout padrão com as cores da Your Home. Use o botão *Imprimir / PDF* e escolha "Salvar como PDF".
  Também mostra o histórico (quem criou, mudou o status ou transferiu).
- **Painel da equipe** (supervisor, gerente e diretor): indicadores do período, produção por analista e
  pontos de atenção (SICAQ vencido ou vencendo em até 30 dias, análises paradas há 5 dias ou mais).
- **Relatórios gerais** (diretor): taxa de aprovação, reprovados, rendas formais e informais, análises/aprovados/reprovados
  por mês, reprovados por motivo, distribuição por status, top construtoras e desempenho por analista.
- **Modo demonstração:** `python demo.py` cria um banco separado com dados fictícios para testar sem mexer nos dados reais.

## 5. Protocolos, distribuição, SLA e tempo de análise (versão 1.2)

**Fluxo:** o supervisor pega o protocolo no Anapro → **Distribuir protocolo** → informa o nº, a **data e hora da solicitação**,
o tipo (**LEAD** ou **MESA**) e o analista → o protocolo aparece na hora em **Minha fila** do analista.

| Tipo | Prazo | Fica amarelo quando faltam |
|---|---|---|
| LEAD | 60 min | 15 min ou menos |
| MESA (prioridade) | 30 min | 10 min ou menos |

- **Cores:** 🔵 azul = no prazo · 🟡 amarelo = prestes a atrasar · 🔴 vermelho = atrasado.
- O **SLA** conta a partir do **horário de solicitação do Anapro** (não do horário da distribuição).
- **Iniciar / Finalizar (v1.2):** o analista clica em **▶ Iniciar** na fila para abrir o protocolo. A partir daí conta também o
  **tempo de análise**. Ao terminar, escolhe o parecer no Status e clica em **✓ Finalizar análise**: o tempo de análise e o SLA param.
  Só "Salvar" guarda os dados sem parar o relógio. Supervisor/gerente/diretor podem **Reabrir** uma análise finalizada.
- **Um processo por vez:** enquanto o analista tiver um protocolo iniciado e não finalizado, o botão Iniciar dos outros fica
  bloqueado. **Exceção: MESA** pode ser iniciada a qualquer momento.
- **Reprova:** ao escolher "Reprovado", é obrigatório informar o motivo: Compromissos financeiros, Dívidas vencidas,
  Pendência interna ou Reprovado simulação.
- **Protocolo já cadastrado:** ao digitar o nº na tela Distribuir, o sistema avisa se ele já existe. Se o anterior já foi finalizado,
  basta confirmar a **redistribuição**: os dados são puxados do histórico, sem recadastrar (o atendimento antigo e seu SLA ficam
  guardados). Se ainda estiver em andamento, use Transferir.
- A contagem é em **minutos corridos**.
- **MESA:** o supervisor escolhe MESA ao distribuir, ou clica em **Marcar MESA** depois (na fila ou na análise). Protocolos MESA
  ficam sempre no topo da fila, com borda magenta e um alerta piscando. Quando chega protocolo novo, a fila toca um aviso sonoro,
  e com "Ativar avisos no computador" também aparece uma notificação do Windows.
- **Transferência:** supervisor, gerente ou diretor transfere pela fila ou pela análise. Tudo fica no **Histórico** da análise
  (quem distribuiu, para quem, quem transferiu, quando virou MESA, em quanto tempo saiu o parecer). O analista vê quem distribuiu.
- **Painel da equipe:** fila agora (azul, amarelo, vermelho, MESA), o que cada analista tem em andamento, % no prazo,
  SLA médio e tempo médio de análise. O **Volume aprovado** mostra o total do mês atual e zera todo dia 1º.
- **Relatórios gerais (diretor):** análises, taxa de aprovação, aprovadas, **reprovados**, **processos com renda informal** e
  **com renda formal**; SLA e tempo de análise; aprovados e reprovados por mês; e o painel **Reprovados por motivo** com filtro por motivo,
  motivos por analista e a lista dos processos.
- Os prazos ficam no começo do `app.py` (`SLA_MIN` e `SLA_AVISO_MIN`), caso queiram mudar.
- O computador que roda o sistema precisa estar com o **horário de Brasília** certo, porque o SLA usa o relógio dele.
- Atualizar de versões anteriores é só substituir os arquivos e manter o `mba_crm.db`: as colunas novas são criadas sozinhas.

## 6. Logo

Para usar o logo oficial, salve-o como **`static/logo.png`**. A versão branca do site funciona bem, porque
o topo é azul-marinho (um logo branco ou claro fica melhor). Sem esse arquivo, o sistema mostra "Your Home · CRÉDITO IMOBILIÁRIO" em texto.

## 7. Backup (importante)

Todos os dados ficam no arquivo **`mba_crm.db`**, dentro da pasta do sistema.
- O Diretor pode baixar uma cópia em *Relatórios gerais → Backup do banco*.
- Recomendação: copie esse arquivo para o Drive ou OneDrive da empresa pelo menos uma vez por semana.
- Não apague o arquivo `.secret_key`: se ele for apagado, todos precisam entrar de novo.

## 8. Acesso fora da empresa (opcional)

Do jeito que está, o sistema funciona na rede interna. Para acessar de fora, dá para publicar em serviços
como Render ou PythonAnywhere. Nesse caso, use HTTPS e defina a variável `MBA_SECRET_KEY`.
Como há CPF e renda de clientes, trate isso com o responsável de TI/LGPD da empresa.
