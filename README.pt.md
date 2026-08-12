<div align="center">

# Proxion

**Mensagens privadas que são realmente suas.**

Bate-papo, voz e vídeo com criptografia de ponta a ponta de verdade, onde suas conversas ficam
em um armazenamento que você controla, e não nos servidores de uma empresa. Construído sobre o
padrão aberto [Solid](https://solidproject.org). Sem número de telefone, sem cadastro, sem
nenhuma empresa no meio.

**Leia isto no seu idioma:** [English](README.md) · [日本語](README.ja.md) · [中文（简体）](README.zh-Hans.md) · [中文（繁體）](README.zh-Hant.md) · [한국어](README.ko.md) · [Deutsch](README.de.md) · [Français](README.fr.md) · [Español](README.es.md) · Português · [Русский](README.ru.md) · [Italiano](README.it.md) · [Polski](README.pl.md) · [Türkçe](README.tr.md) · [Tiếng Việt](README.vi.md) · [Bahasa Indonesia](README.id.md) · [العربية](README.ar.md)

[![CI](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml/badge.svg)](https://github.com/cafeTechne/proxion-messenger/actions/workflows/ci.yml)
![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-blue)
![Solid Protocol](https://img.shields.io/badge/built%20on-Solid%20Protocol-7c4dff)
![E2E encrypted](https://img.shields.io/badge/encryption-end--to--end-e94560)
![WCAG 2.2 AA](https://img.shields.io/badge/accessibility-WCAG%202.2%20AA-4ade80)
![Platforms](https://img.shields.io/badge/platforms-Windows%20%C2%B7%20macOS%20%C2%B7%20Linux%20%C2%B7%20PWA-8598ae)

<img src="landing/assets/screenshot-chat.png" alt="Proxion no desktop: uma conversa criptografada de ponta a ponta, com salas e contatos na barra lateral" width="800">

</div>

## O que é o Proxion?

O Proxion é um mensageiro como os que você já usa, com uma diferença que muda tudo: seus dados
pertencem a você.

Suas mensagens, arquivos e histórico de chamadas ficam no seu próprio **pod Solid**, um espaço
de armazenamento pessoal que você controla, em vez de ficarem presos dentro do aplicativo de
uma empresa. Escolha um provedor de pod gratuito, traga o seu ou hospede você mesmo, e mude
quando quiser. Sua identidade é criada no seu dispositivo, então não há conta para criar nem
nada que possa vazar.

É um mensageiro de verdade, para o dia a dia: salas e mensagens diretas, chamadas de voz e
vídeo com compartilhamento de tela, arquivos, reações, respostas e muito mais, no Windows,
macOS, Linux e na web.

## Obter o Proxion

**Baixe e abra.** Não há nada para configurar nem servidor para rodar.

- **Windows, macOS ou Linux:** vá até a [página de instalação](https://cafetechne.github.io/proxion-messenger/)
  ou a [versão mais recente](../../releases/latest).
- **macOS com [Homebrew](https://brew.sh):** `brew install cafeTechne/proxion/proxion`
- **No seu navegador:** o Proxion também roda como um aplicativo web instalável.

Como o Proxion não é assinado pela Apple nem pela Microsoft (de propósito, para que nenhum
porteiro fique entre você e o seu próprio software), o seu sistema mostra um aviso único na
primeira vez que você o abre. No Windows escolha *Mais informações e depois Executar assim
mesmo*; no macOS *clique com o botão direito e depois Abrir*; o Linux não mostra aviso nenhum.

## O que você pode fazer

- **Enviar mensagens e ligar.** Salas em grupo e conversas privadas um a um, além de chamadas
  de voz e vídeo ponto a ponto com compartilhamento de tela.
- **Guardar seu histórico.** Tudo fica no seu pod, em formato aberto, então é seu para guardar,
  ler com outras ferramentas e levar com você.
- **Conversas de verdade privadas.** As mensagens diretas têm criptografia de ponta a ponta, e
  você pode confirmar que está mesmo falando com o seu contato por uma frase de segurança curta
  que vocês leem em voz alta juntos. As chamadas são criptografadas da mesma forma.
- **Alcance qualquer pessoa no Solid.** Encontre e convide pessoas de todo o ecossistema Solid,
  não só outros usuários do Proxion.
- **Use em qualquer lugar.** Desktop, navegador e celular, com funcionamento offline, em 16
  idiomas incluindo o árabe da direita para a esquerda, e feito para funcionar só com leitor de
  tela e teclado.

<p align="center">
  <img src="landing/assets/screenshot-mobile.png" alt="Proxion rodando em um celular" width="240">
</p>

## Parte do ecossistema Solid

O Proxion é um bom cidadão do Solid, não um jardim murado que apenas usa o Solid por baixo dos
panos. Uma sala que você cria é escrita no formato de chat padrão do Solid, então outros
aplicativos Solid conseguem lê-la e entrar nela.

<img src="landing/assets/interop-sidebyside.png" alt="A mesma sala mostrada lado a lado no Proxion e no navegador de dados do SolidOS, com as mesmas mensagens" width="900">

- **Abra uma sala do Proxion no [SolidOS](https://solidos.org)** e cada mensagem está lá. Isso é
  verificado contra o SolidOS real nos nossos testes, não apenas afirmado.
- **Encontre e convide pessoas pelo WebID delas.** Descubra as salas que alguém hospeda, ou
  deixe um convite na caixa de entrada Solid da pessoa que qualquer aplicativo Solid consegue
  ler.
- **Veja mensagens e convites novos em tempo real,** que chegam até você mesmo com o Proxion
  fechado.
- **Suas salas sobrevivem a qualquer servidor.** A estrutura de uma sala fica no seu pod, então
  ela pode ser reconstruída só a partir do seu pod.

Salas compartilhadas são abertas por design para que outros aplicativos possam lê-las; as
mensagens diretas privadas têm criptografia de ponta a ponta e são, de propósito, legíveis
apenas pelas pessoas que participam delas. O formato de dados completo está documentado em
[docs/POD_DATA_MODEL.md](docs/POD_DATA_MODEL.md), o panorama de compatibilidade aplicativo por
aplicativo em [docs/INTEROP.md](docs/INTEROP.md), e uma auditoria requisito por requisito frente
ao conjunto de especificações do Solid em [docs/SOLID_COMPLIANCE.md](docs/SOLID_COMPLIANCE.md).

## Privado por design

- **Mensagens diretas e chamadas com criptografia de ponta a ponta,** de modo que nenhum relé
  nem servidor no meio consegue lê-las.
- **Seus dados no seu pod, à mostra.** São dados documentados e padronizados, não um bloco
  trancado, então qualquer aplicativo que você autorizar consegue lê-los e você pode ir embora
  quando quiser.
- **Verificável, não só prometido.** Cada download pode ser rastreado até este código-fonte
  público, e milhares de testes automatizados rodam a cada mudança.

Para os detalhes, incluindo o modelo de segurança das chamadas, o modelo de ameaças e como
verificar um download, veja [docs/SECURITY-MODEL.md](docs/SECURITY-MODEL.md),
[docs/CALLS.md](docs/CALLS.md), [SECURITY.md](SECURITY.md) e
[docs/VERIFYING.md](docs/VERIFYING.md).

## Contribuir

O Proxion é de código aberto e as contribuições são realmente bem-vindas, de relatos de bugs a
código. Comece por [CONTRIBUTING.md](CONTRIBUTING.md). Se você vem da comunidade Solid e algo
não interopera do jeito que você espera, esse é exatamente o tipo de problema que queremos
conhecer.

## Para desenvolvedores e quem hospeda por conta própria

A maioria das pessoas deve simplesmente usar o instalador acima. Para mexer no Proxion ou rodar
o seu próprio gateway sempre ativo (por exemplo, para apontar um celular para o seu desktop):

```bash
pip install -e ./proxion-messenger-core[gateway]
cp .env.example .env   # credenciais de pod opcionais; deixe em branco para uso só local
python run_gateway.py
# abra http://localhost:8080
```

Construir um instalador nativo:

```bash
pip install pyinstaller
pip install -e ./proxion-messenger-core[gateway]
python build_sidecar.py           # empacota o gateway para a sua plataforma
cd tauri-app && cargo tauri build # instalador nativo
```

Rodar os testes:

```bash
cd proxion-messenger-core && pytest    # backend
cd web && npm test                     # frontend
```

**Como tudo se encaixa.** O frontend (em `web/`) é servido por um pequeno **gateway** (em
`proxion-messenger-core/`) que guarda suas chaves, fala com o seu pod e se conecta diretamente
aos gateways dos seus contatos. No desktop o gateway vem embutido dentro do aplicativo e inicia
com ele, então você nunca o vê nem instala o Python. O gateway existe porque o Solid cobre
dados e identidade, mas não a entrega ao vivo, a presença ou o estabelecimento de chamadas, o
mesmo papel que um homeserver cumpre para o Matrix ou um servidor SMTP cumpre para o e-mail.
Detalhes em [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) e
[docs/SELF_HOSTING.md](docs/SELF_HOSTING.md).

## Licença

[AGPL-3.0](LICENSE). Livre para usar, hospedar por conta própria, bifurcar e contribuir. Se você
rodar um Proxion modificado como serviço para outras pessoas, precisa publicar suas mudanças. É
esse o ponto: ninguém pode transformá-lo de volta em um silo.
