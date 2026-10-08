"""Conservative LaTeX scientific payload invariants, independent of prose hashes.

This verifies token preservation, not mathematical equivalence. Only explicit
presentation controls are normalized; unsupported rewrites require host review.
"""
from __future__ import annotations
import hashlib,json,re
from collections import Counter
from typing import Any,Dict,List,Tuple

MATH_ENVS={'equation','equation*','align','align*','alignat','alignat*','flalign','flalign*','multline','multline*','gather','gather*','displaymath','math','eqnarray','eqnarray*','IEEEeqnarray','IEEEeqnarray*','IEEEeqnarraybox','IEEEeqnarraybox*','dmath','dmath*','dgroup','dgroup*'}
MATH_WRAPPERS=MATH_ENVS|{'aligned','alignedat','split','multlined','gathered'}
TABLE_ENVS={'tabular','tabular*','tabularx','longtable'}
FLOAT_ENVS={'figure','figure*','table','table*','wrapfigure','wraptable','sidewaysfigure','sidewaystable'}
FONT_DECL={'tiny','scriptsize','footnotesize','small','normalsize','large','Large','LARGE','huge','Huge','bfseries','mdseries','itshape','upshape','slshape','scshape','rmfamily','sffamily','ttfamily','normalfont','centering','raggedright','raggedleft','raggedbottom','flushleft','flushright','FloatBarrier','clearpage','newpage','pagebreak','nopagebreak','noindent','medskip','smallskip','bigskip','hfill','vfill','par','arraybackslash'}
TEXT_WRAPPERS={'textbf','textit','texttt','textrm','textsf','textnormal','emph','underline','mbox','makecell','nolinkurl'}
ID_COMMANDS={'label','ref','eqref','pageref','autoref','cref','Cref','vref','nameref','cite','citep','citet','citealp','citealt','citeauthor','citeyear','citeyearpar','nocite'}
PROTECTED_ARGS={'caption','subcaption','section','subsection','subsubsection','paragraph','title','author','affiliation','thanks','footnote','footnotetext'}
LAYOUT_ONE={'vspace','hspace','vskip','hskip','fontsize','linespread','color','cellcolor','rowcolor','cline','cmidrule','addlinespace','bibliographystyle'}
RULES={'toprule','midrule','bottomrule','hline','hlineB'}
DELIMITER_SIZE_COMMANDS={'left','right'}|{size+suffix for size in ('big','Big','bigg','Bigg') for suffix in ('','l','r','m')}
DELIMITER_COMMANDS={'{','}','|','lbrace','rbrace','langle','rangle','lvert','rvert','lVert','rVert','vert','Vert','lfloor','rfloor','lceil','rceil','backslash'}

def _command(s:str,i:int)->Tuple[str,int]:
 m=re.match(r'\\([A-Za-z@]+|.)',s[i:],re.S)
 if not m:return '',i+1
 return m.group(1),i+len(m.group(0))

def _group(s:str,i:int,opening:str='{',closing:str='}')->Tuple[str,int]:
 while i<len(s) and s[i].isspace():i+=1
 if i>=len(s) or s[i]!=opening:return '',i
 depth=1;j=i+1
 while j<len(s):
  if s[j]=='\\':_,j=_command(s,j);continue
  if s[j]==opening:depth+=1
  elif s[j]==closing:
   depth-=1
   if not depth:return s[i+1:j],j+1
  j+=1
 return '',i

def _optional(s:str,i:int)->int:
 while i<len(s) and s[i].isspace():i+=1
 if i<len(s) and s[i]=='[':_,j=_group(s,i,'[',']');return j
 return i

def _literal_dimension(value:str)->bool:
 # A bounded spelling of a TeX length, not arbitrary macros or expressions.
 number=r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)'
 return bool(re.fullmatch(r'\s*(?:'+number+r'\s*(?:pt|em|ex|cm|mm|in|bp|pc|dd|cc|sp)|(?:'+number+r'\s*)?\\(?:linewidth|columnwidth|textwidth|textheight))\s*',value))

def minipage_layout_end(s:str,i:int)->int|None:
 """End of known minipage presentation arguments; unknown forms stay opaque."""
 options=[];pos=i
 for _ in range(3):
  while pos<len(s) and s[pos].isspace():pos+=1
  if pos>=len(s) or s[pos]!='[':break
  value,end=_group(s,pos,'[',']')
  if end==pos:return None
  options.append(value.strip());pos=end
 if options and options[0] not in {'t','c','b'}:return None
 if len(options)>1 and options[1] and not _literal_dimension(options[1]):return None
 if len(options)>2 and options[2] not in {'t','c','b','s'}:return None
 width,end=_group(s,pos)
 return end if end>pos and _literal_dimension(width) else None

def literal_long_identifier(value:str)->bool:
 # nolinkurl has special TeX character rules. Normalize only the literal
 # alphanumeric long identifiers used by D1; macros/URL punctuation stay opaque.
 return bool(re.fullmatch(r'[A-Za-z][A-Za-z0-9]{39,}',value))

def shadowed_literal_url_commands(source:str)->frozenset[str]:
 # Direct definitions of these newly supported wrappers invalidate their
 # standard presentation meaning. Inspect the whole source, not a body chunk.
 names=set()
 for record in _macro_definitions(active_source(source)):
  target=record[1] if record[0] in {'def','gdef','edef','xdef','let'} else record[2]
  name=target.lstrip('\\')
  if name in {'nolinkurl','urlstyle'}:names.add(name)
 return frozenset(names)

def _shadowed_presentation_commands(source:str)->frozenset[str]:
 # Newly normalized controls must keep their literal invocation when the
 # source directly defines them. This is not macro expansion or alias tracing.
 names=set(shadowed_literal_url_commands(source))
 for record in _macro_definitions(source):
  target=record[1] if record[0] in {'def','gdef','edef','xdef','let'} else record[2]
  name=target.lstrip('\\')
  if name in DELIMITER_SIZE_COMMANDS|{'parbox'}:names.add(name)
 return frozenset(names)

def _shadowed_literal_url_payload(source:str)->tuple[Any,...]:
 shadowed=shadowed_literal_url_commands(source);records=[];i=0
 while i<len(source):
  if source[i]!='\\':i+=1;continue
  name,end=_command(source,i)
  if name in shadowed:
   star=end<len(source) and source[end]=='*';pos=end+int(star)
   arg,last=_group(source,pos)
   records.append((name,star,re.sub(r'\s+',' ',arg).strip() if last>pos else None))
   i=last if last>pos else pos;continue
  i=end
 return tuple(records)

def active_source(s:str)->str:
 chars=list(s);i=0
 while i<len(s):
  if s[i]=='\\':
   name,j=_command(s,i)
   if name=='verb':
    if j<len(s) and s[j]=='*':j+=1
    if j<len(s):
     end=s.find(s[j],j+1);end=len(s) if end<0 else end+1
     for k in range(i,end):chars[k]='\n' if s[k]=='\n' else ' '
     i=end;continue
   if name=='begin':
    env,end=_group(s,j)
    if env in {'verbatim','Verbatim','lstlisting','minted','comment','filecontents','filecontents*'}:
     marker='\\end{'+env+'}';endpos=s.find(marker,end);stop=len(s) if endpos<0 else endpos+len(marker)
     for k in range(i,stop):chars[k]='\n' if s[k]=='\n' else ' '
     i=stop;continue
   i=j;continue
  if s[i]=='%':
   end=s.find('\n',i);end=len(s) if end<0 else end
   for k in range(i,end):chars[k]=' '
   i=end;continue
  i+=1
 return ''.join(chars)

def _envs(s:str)->List[Dict[str,Any]]:
 stack=[];out=[]
 for m in re.finditer(r'\\(begin|end)\s*\{([^{}]+)\}',s):
  if m.group(1)=='begin':stack.append((m.group(2),m.start(),m.end()))
  elif stack and stack[-1][0]==m.group(2):
   name,start,body=stack.pop();out.append({'name':name,'start':start,'body':body,'tail':m.start(),'end':m.end()})
 return sorted(out,key=lambda x:x['start'])

def _math_like_environment(name:str)->bool:
 return bool(re.search(r'(?:math|equation|eqn|align|gather|multline)',name,re.I))

def _math_spans(s:str,envs:List[Dict[str,Any]])->List[Dict[str,Any]]:
 spans=[]
 for e in envs:
  if (e['name'] in MATH_ENVS or _math_like_environment(e['name'])) and not any(p['start']<=e['start']<p['end'] for p in spans):
   spans.append({**e,'text':s[e['body']:e['tail']],'opaque':e['name'] not in MATH_ENVS,'numbered':e['name'] not in {'math','displaymath','IEEEeqnarraybox','IEEEeqnarraybox*'} and not e['name'].endswith('*')})
 i=0
 while i<len(s):
  cover=next((e for e in spans if e['start']<=i<e['end']),None)
  if cover:i=cover['end'];continue
  if s[i]=='\\':
   name,j=_command(s,i)
   if name=='ensuremath':
    arg,end=_group(s,j)
    if end>j:
     spans.append({'start':i,'end':end,'text':arg,'numbered':False});i=end;continue
   if name in {'(','['}:
    endmark='\\'+(')' if name=='(' else ']');end=s.find(endmark,j)
    if end>=0:spans.append({'start':i,'end':end+2,'text':s[j:end],'numbered':False});i=end+2;continue
   i=j;continue
  if s[i]=='$':
   delim='$$' if s.startswith('$$',i) else '$';j=i+len(delim);k=j
   while k<len(s):
    if s[k]=='\\':_,k=_command(s,k);continue
    if s.startswith(delim,k):break
    k+=1
   if k<len(s):spans.append({'start':i,'end':k+len(delim),'text':s[j:k],'numbered':False});i=k+len(delim);continue
  i+=1
 ordered=sorted(spans,key=lambda x:(x['start'],-x['end']));outer=[]
 for span in ordered:
  if not any(parent['start']<=span['start'] and span['end']<=parent['end'] for parent in outer):outer.append(span)
 return outer

def _roman_stack(s:str)->str:
 # Only the existing bounded long-roman-identifier presentation rewrite.
 pattern=r'_\{\\substack\{((?:\\mathrm\{[A-Za-z][A-Za-z0-9]*\})(?:\\\\\s*\\mathrm\{[A-Za-z0-9]+\})+)\}\}'
 def replace(m):
  chunks=re.findall(r'\\mathrm\{([A-Za-z0-9]+)\}',m.group(1));word=''.join(chunks)
  return '_{\\mathrm{'+word+'}}' if len(word)>=40 else m.group(0)
 return re.sub(pattern,replace,s)

def _presentation_delimiter(s:str,i:int,name:str)->Tuple[str,int]|None:
 while i<len(s) and s[i].isspace():i+=1
 if i>=len(s):return None
 if s[i] in '()[]|':return s[i],i+1
 # The dot is an invisible delimiter only in explicit left/right syntax.
 if s[i]=='.' and name in {'left','right'}:return '',i+1
 if s[i]=='\\':
  delimiter,end=_command(s,i)
  if delimiter in DELIMITER_COMMANDS:return '\\'+delimiter,end
 return None

def _math_tokens(s:str,*,opaque_commands:frozenset[str]=frozenset())->Tuple[str,...]:
 s=_roman_stack(s)
 s=re.sub(r'([_^])\{([A-Za-z0-9]|\\[A-Za-z]+)\}',r'\1\2',s)
 s=re.sub(r'(?<=[=+\-<>])\{\}', '', s)
 out=[];i=0;protected=[]
 while i<len(s):
  if s[i].isspace():i+=1;continue
  if s[i]=='\\':
   name,j=_command(s,i)
   if not protected and name in DELIMITER_SIZE_COMMANDS and name not in opaque_commands:
    delimiter=_presentation_delimiter(s,j,name)
    if delimiter is not None:
     token,end=delimiter
     if token:out.append(token)
     i=end;continue
   if name=='ensuremath' and not protected:
    arg,end=_group(s,j)
    if end>j:out.extend(_math_tokens(arg,opaque_commands=opaque_commands));i=end;continue
   if name in {'begin','end'}:
    env,k=_group(s,j)
    if env in MATH_WRAPPERS:
     if name=='begin':
      k=_optional(s,k)
      if env in {'multlined'}:k=_optional(s,k)
      if env in {'alignat','alignat*','alignedat'}:_,k=_group(s,k)
     i=k;continue
    if name=='begin':protected.append(env)
    elif protected and protected[-1]==env:protected.pop()
   if not protected and name in {'\\','quad','qquad',',','!',';',':','enspace','thinspace','displaystyle','textstyle','scriptstyle','scriptscriptstyle'}:
    i=_optional(s,j) if name=='\\' else j;continue
   if name in {'text','textnormal','textrm','textbf','textit','texttt','operatorname','mathrm'}:
    arg,end=_group(s,j)
    if end>j:
     out.extend(('\\'+name,'{',re.sub(r'\s+', ' ', arg).strip(),'}'));i=end;continue
   if name in {'hspace','vspace'}:
    if j<len(s) and s[j]=='*':j+=1
    _,k=_group(s,j);i=k if k>j else j;continue
   out.append('\\'+name);i=j;continue
  if s[i]=='&' and not protected:i+=1;continue
  out.append(s[i]);i+=1
 return tuple(out)

def _safe_caption_setup(argument:str)->bool:
 fonts={'tiny','scriptsize','footnotesize','small','normalsize','large','Large','LARGE',
        'huge','Huge','bf','it','sl','sc','sf','rm','tt','md','up','normalfont'}
 for field in _split_top(argument,','):
  if '=' not in field:return False
  key,value=field.split('=',1);key=key.strip();value=value.strip()
  if value.startswith('{'):
   body,end=_group(value,0)
   if end==len(value):value=body.strip()
  if key in {'font','labelfont','textfont'}:
   if not set(v.strip() for v in value.split(','))<=fonts:return False
  elif key in {'skip','aboveskip','belowskip'}:
   if not re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*(?:pt|em|ex|cm|mm|in)',value):return False
  else:return False
 return True

def _safe_float_counter(name:str,value:str)->bool:
 return name.strip() in {'topnumber','bottomnumber','totalnumber','dbltopnumber'} and bool(re.fullmatch(r'\s*\d+\s*',value))

def _strip_layout(s:str,*,text_wrappers:bool=False,opaque_commands:frozenset[str]=frozenset())->str:
 s=re.sub(r'\\tabcolsep\s*(?:=\s*)?[+-]?(?:\d+(?:\.\d*)?|\.\d+)\s*(?:pt|em|ex|cm|mm|in)\b','',s)
 s=re.sub(r'\\(?:widowpenalty|clubpenalty|displaywidowpenalty|brokenpenalty|interlinepenalty)\s*(?:=\s*)?[+-]?\d+\b','',s)
 s=re.sub(r'\\(?:linebreak|nolinebreak)\s*(?:\[[0-4]\])?','',s)
 out=[];i=0
 while i<len(s):
  if s[i]!='\\':out.append(s[i]);i+=1;continue
  name,j=_command(s,i);star=j<len(s) and s[j]=='*';j+=int(star)
  if name in opaque_commands:
   # The complete source has directly redefined this presentation command.
   # Preserve its literal invocation in each caption/cell/argument context,
   # including nested arguments; an unanchored inventory alone misses moves.
   last=j
   while True:
    pos=last
    while pos<len(s) and s[pos].isspace():pos+=1
    if pos<len(s) and s[pos] in '[{':
     opening=s[pos];_,end=_group(s,pos,opening,']' if opening=='[' else '}')
     if end>pos:last=end;continue
    break
   out.append(s[i:last]);i=last;continue
  opt=_optional(s,j)
  if name=='looseness' and not star:
   # Only the bounded literal paragraph adjustments used by A1 are layout.
   # Dynamic values and larger changes remain visible to preservation checks.
   value=re.match(r'\s*(?:=\s*)?([+-]?[01])(?=\s|}|$)',s[j:])
   if value:i=j+value.end();continue
  if name in FONT_DECL|RULES:
   if name=='par' and text_wrappers:out.append(' ')
   i=opt;continue
  if name=='urlstyle' and not star:
   body,end=_group(s,j)
   if end>j and body=='same':i=end;continue
  if name=='captionsetup':
   body,end=_group(s,opt)
   if end>opt and _safe_caption_setup(body):i=end;continue
  if name=='setcounter':
   counter,k=_group(s,j);value,end=_group(s,k)
   if end>k and _safe_float_counter(counter,value):i=end;continue
  if name in {'setlength','addtolength'}:
   arg,k=_group(s,j);_,end=_group(s,k)
   if arg in {'\\tabcolsep','\\arrayrulewidth','\\textheight','\\textwidth','\\columnsep','\\columnwidth','\\linewidth','\\abovecaptionskip','\\belowcaptionskip','\\floatsep','\\textfloatsep','\\intextsep','\\parskip','\\parindent','\\baselineskip'} and end>k:i=end;continue
  if name in {'renewcommand','newcommand','providecommand'}:
   arg,k=_group(s,j);k=_optional(s,k);_,end=_group(s,k)
   if arg in {'\\arraystretch','\\topfraction','\\bottomfraction','\\textfraction','\\floatpagefraction','\\dbltopfraction','\\dblfloatpagefraction'} and end>k:i=end;continue
  if name in {'usepackage','RequirePackage','documentclass','bibliographystyle'}:
   _,end=_group(s,opt);i=end if end>opt else opt;continue
  if name in LAYOUT_ONE:
   _,end=_group(s,opt);i=end if end>opt else opt
   if name=='fontsize':_,i=_group(s,i)
   continue
  if name=='parbox' and not star:
   # The standard [pos][height][inner-pos]{width}{body} header is layout.
   # Dynamic lengths and unsupported options retain their literal payload.
   stop=minipage_layout_end(s,j)
   if stop is not None:
    pos=stop
    while pos<len(s) and s[pos].isspace():pos+=1
    body,end=_group(s,pos)
    if pos<len(s) and s[pos]=='{' and end>pos:out.append(_strip_layout(body,text_wrappers=text_wrappers,opaque_commands=opaque_commands));i=end;continue
   out.append(s[i:j]);i=j;continue
  if name in {'resizebox','scalebox','rotatebox','raisebox'}:
   _,k=_group(s,opt)
   if name=='resizebox':_,k=_group(s,k)
   if name=='raisebox':k=_optional(s,k);k=_optional(s,k)
   body,end=_group(s,k)
   if end>k:out.append(_strip_layout(body,text_wrappers=text_wrappers,opaque_commands=opaque_commands));i=end;continue
  if name in {'includegraphics'}:
   _,end=_group(s,opt);i=end if end>opt else opt;continue
  if name in {'begin','end'}:
   env,end=_group(s,j)
   if name=='begin' and env=='minipage':
    stop=minipage_layout_end(s,end)
    if stop is not None:i=stop;continue
    # Do not discard a dynamic or malformed environment header.
    out.append(s[i:end]);i=end;continue
   if env and env not in TABLE_ENVS:
    i=end
    if name=='begin' and env in FLOAT_ENVS:
     i=_optional(s,i)
     if env in {'wrapfigure','wraptable'}:_,i=_group(s,i);_,i=_group(s,i)
    continue
  if name in PROTECTED_ARGS and not text_wrappers:
   _,end=_group(s,opt);i=end if end>opt else opt;continue
  if name in ID_COMMANDS:
   _,end=_group(s,opt);i=end if end>opt else opt;continue
  if name in {'footnotemark'}:i=opt;continue
  if name in {'footnotetext','footnote'}:out.append('\\'+name);i=opt;continue
  if text_wrappers and name=='makecell':
   # Standard makecell alignment only changes presentation. Keep dynamic or
   # complex column specifications (and unsupported starred forms) opaque.
   pos=j
   while pos<len(s) and s[pos].isspace():pos+=1
   known=not star
   if pos<len(s) and s[pos]=='[':
    alignment,end=_group(s,pos,'[',']')
    known=known and end>pos and bool(re.fullmatch(r'(?:[lcrtb]|[tb][lcr]|[lcr][tb]|cc)',alignment))
    if end>pos:pos=end
   while pos<len(s) and s[pos].isspace():pos+=1
   body,end=_group(s,pos)
   if end>pos:
    out.append(_strip_layout(body,text_wrappers=True,opaque_commands=opaque_commands) if known else s[i:end])
    i=end;continue
   out.append(s[i:j]);i=j;continue
  if name=='nolinkurl':
   body,end=_group(s,j)
   if end>j:
    out.append(body if not star and literal_long_identifier(body) else s[i:end])
    i=end;continue
   out.append(s[i:j]);i=j;continue
  if text_wrappers and name in TEXT_WRAPPERS|{'shortstack'}:
   body,end=_group(s,opt)
   if end>opt:out.append(_strip_layout(body,text_wrappers=True,opaque_commands=opaque_commands));i=end;continue
  if text_wrappers and name in {'multirow','multicolumn'}:
   span,k=_group(s,opt);spec,k=_group(s,k);body,end=_group(s,k)
   if end>k:
    insertion=_colspec_payload(spec,opaque_commands=opaque_commands) if name=='multicolumn' else ()
    marker='\\'+name+'{'+span+'}'
    if insertion:marker+=repr(insertion)
    out.append(marker+'{'+_strip_layout(body,text_wrappers=True,opaque_commands=opaque_commands)+'}');i=end;continue
  if text_wrappers and name=='\\':i=opt;continue
  out.append(s[i:j]);i=j
 return ''.join(out)

def _text_tokens(s:str,*,opaque_commands:frozenset[str]=frozenset())->Tuple[Any,...]:
 s=_strip_layout(s,text_wrappers=True,opaque_commands=opaque_commands);envs=_envs(s);math=_math_spans(s,envs);out=[];cursor=0
 for m in math:
  out.extend(c for c in s[cursor:m['start']] if not c.isspace());out.append(('math',_math_tokens(m['text'],opaque_commands=opaque_commands)));cursor=m['end']
 out.extend(c for c in s[cursor:] if not c.isspace())
 while len(out)>=2 and out[0]=='{' and out[-1]=='}':
  depth=0;whole=True
  for i,t in enumerate(out):
   if t=='{':depth+=1
   elif t=='}':depth-=1
   if depth==0 and i<len(out)-1:whole=False;break
  if not whole:break
  out=out[1:-1]
 return tuple(out)

def _colspec_payload(spec:str,*,opaque_commands:frozenset[str]=frozenset())->Tuple[Any,...]:
 # Column alignment/width are layout; @/! insertions can render scientific text.
 out=[];i=0;column=0
 while i<len(spec):
  c=spec[i]
  if c.isspace() or c in '|:':i+=1;continue
  if c in 'lcrX':column+=1;i+=1;continue
  if c in 'pmb':column+=1;_,i=_group(spec,i+1);continue
  if c in '@!><':
   body,end=_group(spec,i+1)
   if end==i+1:out.append((column,c));i+=1;continue
   body=re.sub(r'\\extracolsep\s*\{\s*\\fill\s*\}', '', body)
   payload=_text_tokens(body,opaque_commands=opaque_commands)
   if payload:out.append((column,c,payload))
   i=end;continue
  if c=='*':
   count,j=_group(spec,i+1);body,end=_group(spec,j)
   if count.isdigit() and end>j and int(count)<=100:
    # Preserve insertions in repeated column specs without treating widths as data.
    repeated=_colspec_payload(body,opaque_commands=opaque_commands)
    if repeated:out.append((column,'repeat',count,repeated))
    column+=int(count)*next((v[1] for v in repeated if v[0]=='columns'),0);i=end;continue
  if c=='\\':name,j=_command(spec,i);out.append((column,'unknown','\\'+name));i=j;continue
  out.append((column,'unknown',c));i+=1
 return (('columns',column),)+tuple(out)

def _table_rows(body:str,*,opaque_commands:frozenset[str]=frozenset())->Tuple[Any,...]:
 return tuple(tuple(_text_tokens(cell,opaque_commands=opaque_commands) for cell in _split_top(row,'&'))
              for row in _split_top(body,'\\\\') if _text_tokens(row,opaque_commands=opaque_commands))

def _split_top(s:str,delimiter:str)->List[str]:
 parts=[];start=0;depth=0;i=0
 while i<len(s):
  if depth==0 and s.startswith(delimiter,i):
   parts.append(s[start:i]);i+=len(delimiter)
   if delimiter=='\\\\':i=_optional(s,i)
   start=i;continue
  if s[i]=='\\':_,i=_command(s,i);continue
  if s[i]=='{':depth+=1
  elif s[i]=='}':depth-=1
  i+=1
 parts.append(s[start:]);return parts

def _ids(s:str)->Tuple[Any,...]:
 facts=[];i=0
 while i<len(s):
  if s[i]!='\\':i+=1;continue
  name,j=_command(s,i)
  if name in ID_COMMANDS:
   if j<len(s) and s[j]=='*':j+=1
   j=_optional(s,j);j=_optional(s,j);arg,end=_group(s,j)
   if end>j:
    family='cite' if name.startswith('cite') or name=='nocite' else 'ref' if name!='label' else 'label'
    values=arg.split(',') if family in {'cite','ref'} else [arg]
    facts.extend((family,v.strip()) for v in values);i=end;continue
  i=j
 return tuple(sorted(Counter(facts).items()))

def _configuration_payload(s:str,*,opaque_commands:frozenset[str]=frozenset())->Tuple[Any,...]:
 records=[];i=0
 while i<len(s):
  if s[i]!='\\':i+=1;continue
  name,j=_command(s,i)
  if name=='captionsetup':
   j=_optional(s,j);arg,end=_group(s,j)
   if end>j:
    if not _safe_caption_setup(arg):records.append((name,_text_tokens(arg,opaque_commands=opaque_commands)))
    i=end;continue
  if name=='setcounter':
   arg,k=_group(s,j);value,end=_group(s,k)
   if end>k:
    if not _safe_float_counter(arg,value):records.append((name,arg,_text_tokens(value,opaque_commands=opaque_commands)))
    i=end;continue
  i=j
 return tuple(records)

def _macro_definitions(s:str)->Tuple[Any,...]:
 # No expansion or guessed equivalence. Changing an unknown declaration is a
 # review/rollback boundary, even when the formula using it stays unchanged.
 layout_macros={'\\arraystretch','\\topfraction','\\bottomfraction','\\textfraction',
                '\\floatpagefraction','\\dbltopfraction','\\dblfloatpagefraction'}
 declarations={'newcommand','renewcommand','providecommand','DeclareRobustCommand',
               'DeclareMathOperator','newenvironment','renewenvironment','provideenvironment',
               'newcolumntype'}
 primitives={'def','gdef','edef','xdef'}
 records=[];i=0
 while i<len(s):
  if s[i]!='\\':i+=1;continue
  name,j=_command(s,i)
  if name in declarations:
   star=j<len(s) and s[j]=='*';j+=int(star)
   target,end=_group(s,j)
   if end==j:
    k=j
    while k<len(s) and s[k].isspace():k+=1
    if k<len(s) and s[k]=='\\':target_name,end=_command(s,k);target='\\'+target_name
   k=end;signature=[]
   for _ in range(2):
    pos=k
    while pos<len(s) and s[pos].isspace():pos+=1
    if pos<len(s) and s[pos]=='[':arg,k=_group(s,pos,'[',']');signature.append(arg)
   body,end=_group(s,k)
   if end>k:
    bodies=[body]
    if name.endswith('environment'):
     tail,last=_group(s,end)
     if last>end:bodies.append(tail);end=last
    known_layout = target in layout_macros and not signature and len(bodies)==1 and re.fullmatch(r'\s*(?:\d+(?:\.\d*)?|\.\d+)\s*',body)
    if not known_layout:
     records.append((name,star,target,tuple(signature),tuple(re.sub(r'\s+',' ',v).strip() for v in bodies)))
    i=end;continue
  if name in primitives:
   k=j
   while k<len(s) and s[k].isspace():k+=1
   if k<len(s) and s[k]=='\\':
    target,end=_command(s,k);brace=s.find('{',end)
    if brace>=0:
     body,last=_group(s,brace)
     if last>brace:
      known_layout = '\\'+target in layout_macros and not s[end:brace].strip() and re.fullmatch(r'\s*(?:\d+(?:\.\d*)?|\.\d+)\s*',body)
      if not known_layout:
       records.append((name,'\\'+target,s[end:brace].strip(),re.sub(r'\s+',' ',body).strip()))
      i=last;continue
  if name=='let':
   k=j
   while k<len(s) and s[k].isspace():k+=1
   if k<len(s) and s[k]=='\\':
    target,end=_command(s,k);k=end
    while k<len(s) and (s[k].isspace() or s[k]=='='):k+=1
    value,last=_command(s,k) if k<len(s) and s[k]=='\\' else (s[k:k+1],k+1)
    records.append((name,target,value));i=last;continue
  i=j
 return tuple(records)

def _payload(s:str)->Dict[str,Any]:
 s=active_source(s);opaque_commands=_shadowed_presentation_commands(s);envs=_envs(s);floats=[e for e in envs if e['name'] in FLOAT_ENVS];tables=[e for e in envs if e['name'] in TABLE_ENVS];math=_math_spans(s,envs)
 def context(start):
  parents=[e for e in floats+[t for t in tables if t['name']=='longtable'] if e['start']<=start<e['end']]
  if not parents:return None
  parent=min(parents,key=lambda e:e['end']-e['start']);block=s[parent['body']:parent['tail']]
  labels=tuple(re.findall(r'\\label\s*\{([^{}]+)\}',block));graphics=tuple(re.findall(r'\\includegraphics(?:\[[^\]]*\])?\s*\{([^{}]+)\}',block))
  return (labels,graphics)
 math_body=[];math_float=[]
 for m in math:
  record=('opaque_math_environment',m['name'],re.sub(r'\s+',' ',m['text']).strip()) if m.get('opaque') else (m['numbered'],_math_tokens(m['text'],opaque_commands=opaque_commands));key=context(m['start'])
  if key is None:math_body.append(record)
  else:math_float.append((key,record))
 table_records=[]
 for e in tables:
  body=s[e['body']:e['tail']];offset=_optional(body,0);spec=''
  for _ in range(2 if e['name'] in {'tabular*','tabularx'} else 1):spec,offset=_group(body,offset)
  body=body[offset:]
  body=re.sub(r'\\(?:cmidrule|cline)(?:\[[^\]]*\])?\{[^{}]*\}','',body)
  body=re.sub(r'\\(?:toprule|midrule|bottomrule|hline)(?:\[[^\]]*\])?','',body)
  key=context(e['start'])
  if e['name']=='longtable':
   # A generated repeated head is removable only if it exactly matches the first
   # head's data. Unknown footer text remains protected rather than discarded.
   match=re.search(r'\\endfirsthead(.*?)\\endhead(.*?)\\endfoot(.*?)\\endlastfoot',body,re.S)
   if match:
    first=body[:match.start()]
    caption=re.search(r'\\caption(?:\[[^\]]*\])?\s*\{',first)
    labels=tuple(re.findall(r'\\label\s*\{([^{}]+)\}',first))
    if key is None and labels:key=(labels,())
    if caption:
     _,caption_end=_group(first,caption.end()-1)
     first=first[:caption.start()]+first[caption_end:]
    first_rows=_table_rows(first,opaque_commands=opaque_commands);repeat_rows=_table_rows(match.group(1),opaque_commands=opaque_commands)
    footer=_text_tokens(match.group(2),opaque_commands=opaque_commands);last_footer=_text_tokens(match.group(3),opaque_commands=opaque_commands)
    generated_footer=re.fullmatch(r'\s*\\multicolumn\{\d+\}\{r\}\{Continued on next page\}\s*(?:\\\\)?\s*',match.group(2))
    if repeat_rows!=first_rows:table_records.append((key,('repeated_head',repeat_rows)))
    if footer and not generated_footer:table_records.append((key,('footer',footer)))
    if last_footer:table_records.append((key,('last_footer',last_footer)))
    body=first+body[match.end():]
  rows=_table_rows(body,opaque_commands=opaque_commands)
  table_records.append((key,rows,_colspec_payload(spec,opaque_commands=opaque_commands)))
 # Numeric payload outside protected math/table blocks, preserving body order.
 excluded=sorted([(e['start'],e['end']) for e in tables]+[(m['start'],m['end']) for m in math])
 chars=list(s)
 for start,end in excluded:chars[start:end]=' '*(end-start)
 remainder=''.join(chars);numeric_body=[];numeric_float=[];arguments=[]
 def numbers(text):return tuple(re.findall(r'(?<![A-Za-z])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?',_strip_layout(text,opaque_commands=opaque_commands)))
 body_parts=[];cursor=0
 for e in floats:
  if e['start']<cursor:continue
  body_parts.append(remainder[cursor:e['start']]);values=numbers(remainder[e['start']:e['end']])
  if values:numeric_float.append((context(e['start']),values))
  cursor=e['end']
 body_parts.append(remainder[cursor:]);numeric_body=numbers(''.join(body_parts))
 i=0
 while i<len(s):
  if s[i]!='\\':i+=1;continue
  name,j=_command(s,i)
  if name in PROTECTED_ARGS:
   j=_optional(s,j);arg,end=_group(s,j)
   if end>j:arguments.append((context(i),name,_text_tokens(arg,opaque_commands=opaque_commands)));i=end;continue
  i=j
 return {'configuration_arguments':_configuration_payload(s,opaque_commands=opaque_commands),'macro_definitions':_macro_definitions(s),'shadowed_literal_url_invocations':_shadowed_literal_url_payload(s),'math_body':tuple(math_body),'math_floats':tuple(sorted(Counter(math_float).items(),key=repr)),'tables':tuple(sorted(Counter(table_records).items(),key=repr)),'identifiers':_ids(s),'numbers_body':numeric_body,'numbers_floats':tuple(sorted(Counter(numeric_float).items(),key=repr)),'protected_arguments':tuple(sorted(Counter(arguments).items(),key=repr))}

def scientific_payload_diff(original:str,repaired:str)->Dict[str,Any]:
 limits='Conservative token invariants, not mathematical equivalence; No macro expansion or mathematical equivalence proof; exotic TeX definitions/restructuring require host review. Cross-file checking requires callers to compare every modified source file.'
 def summary(value):
  return {'sha256':hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode('utf-8')).hexdigest(),'component_counts':{k:len(v) for k,v in value.items()}}
 try:
  before=_payload(original);after=_payload(repaired);changed=[key for key in before if before[key]!=after[key]]
  before_summary=summary(before);after_summary=summary(after)
 except Exception as exc:
  # Checker failure is neither a successful check nor confirmed damage. Keep
  # legacy callers fail closed via checked=False, with a separate diagnosis.
  return {'checked':False,'status':'unavailable','before':None,'after':None,'changed_components':[],
          'regression_reasons':['Scientific checker did not complete: '+type(exc).__name__],
          'assessment':{'classification':'checker_error','scope':'protected_token_invariants',
                        'semantic_damage_proven':False,'requires_host_review':True,
                        'confirmed_changed_components':[],'review_changed_components':[]},
          'limits':limits}
 def supported_math_records(value):
  if isinstance(value,tuple):
   if len(value)==2 and isinstance(value[0],bool):return [value]
   if value and value[0]=='opaque_math_environment':return []
   return [record for item in value for record in supported_math_records(item)]
  return []
 review=[]
 for key in changed:
  if key in {'macro_definitions','configuration_arguments','shadowed_literal_url_invocations'}:
   review.append(key)
  elif key in {'math_body','math_floats'} and supported_math_records(before[key])==supported_math_records(after[key]):
   review.append(key)
 confirmed=[key for key in changed if key not in review]
 # A confirmed literal invariant change is not a mathematical damage proof.
 # Unsupported declarations/opaque environments retain a separate review
 # diagnosis; neither diagnosis relaxes the legacy failed status.
 classification='confirmed_payload_change' if confirmed else 'manual_review' if changed else 'supported_preservation'
 return {'checked':True,'status':'failed' if changed else 'pass','before':before_summary,'after':after_summary,
         'changed_components':changed,'regression_reasons':['Scientific payload changed: '+k for k in changed],
         'assessment':{'classification':classification,'scope':'protected_token_invariants',
                       'semantic_damage_proven':False,'requires_host_review':bool(changed),
                       'confirmed_changed_components':confirmed,'review_changed_components':review},
         'limits':limits}
