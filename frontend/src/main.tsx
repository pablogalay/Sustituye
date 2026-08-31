import React,{useEffect,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {AddRounded,AnalyticsRounded,ArrowForwardRounded,CalendarMonthRounded,CheckCircleRounded,CloseRounded,DashboardRounded,DeleteForeverRounded,DescriptionRounded,DownloadRounded,HistoryRounded,LogoutRounded,MenuRounded,PeopleAltRounded,SchoolRounded,ShieldRounded,StorageRounded,SyncRounded,UploadRounded,WarningAmberRounded} from '@mui/icons-material';
import {Alert,AppBar,Avatar,Box,Button,Card,Chip,CircularProgress,CssBaseline,Dialog,DialogActions,DialogContent,DialogContentText,DialogTitle,Divider,Drawer,FormControl,IconButton,InputLabel,List,ListItemButton,ListItemIcon,ListItemText,ListSubheader,MenuItem,Paper,Select,Stack,TextField,ThemeProvider,Toolbar,Tooltip,Typography,createTheme,useMediaQuery} from '@mui/material';

type Teacher={id:number;first_name:string;last_name:string;email:string;active:boolean};
type Group={id:number;name:string};
type Classroom={id:number;name:string};
type Slot={id:number;weekday:string;period_number:number;start_time:string;end_time:string};
type User={sub:string;role:'admin'|'teacher';teacher_id?:number};
type Absence={id:number;date:string;timeslot_id:number;absent_teacher_id:number;class_group_id:number;classroom_id:number;task_left:string;observations?:string|null;substitute_teacher_id?:number|null;created_at?:string};
const drawerWidth=272;
const api=async(path:string,opts:RequestInit={})=>{const r=await fetch('/api'+path,{headers:{'Content-Type':'application/json',...(localStorage.token?{Authorization:`Bearer ${localStorage.token}`}:{ }),...opts.headers},...opts});if(!r.ok){let detail:string|undefined;try{detail=(await r.json()).detail}catch{}throw new Error(detail||`No se ha podido completar la acción (HTTP ${r.status})`)}return r.status===204?null:r.json()};
const authHeaders=():Record<string,string>=>(localStorage.token?{Authorization:`Bearer ${localStorage.token}`}:{});
const apiDownload=async(path:string,fallbackName:string)=>{
  const r=await fetch('/api'+path,{headers:authHeaders()});
  if(!r.ok){let detail:string|undefined;try{detail=(await r.json()).detail}catch{}throw new Error(detail||`No se ha podido completar la acción (HTTP ${r.status})`)}
  const disposition=r.headers.get('content-disposition');
  const match=disposition&&/filename="?([^"]+)"?/.exec(disposition);
  const filename=match?match[1]:fallbackName;
  const blob=await r.blob();
  const url=URL.createObjectURL(blob);
  const link=document.createElement('a');
  link.href=url;link.download=filename;document.body.appendChild(link);link.click();link.remove();
  URL.revokeObjectURL(url);
};
const apiUpload=async(path:string,file:File)=>{
  const body=new FormData();body.append('file',file);
  const r=await fetch('/api'+path,{method:'POST',headers:authHeaders(),body});
  if(!r.ok){let detail:string|undefined;try{detail=(await r.json()).detail}catch{}throw new Error(detail||`No se ha podido completar la acción (HTTP ${r.status})`)}
  return r.status===204?null:r.json();
};
const theme=createTheme({palette:{mode:'light',primary:{main:'#4f46e5'},secondary:{main:'#0f766e'},background:{default:'#f6f7fb',paper:'#fff'},text:{primary:'#172033',secondary:'#667085'},success:{main:'#15803d'},warning:{main:'#d97706'}},shape:{borderRadius:14},typography:{fontFamily:'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',h4:{fontWeight:750,letterSpacing:'-0.035em'},h5:{fontWeight:720,letterSpacing:'-0.025em'},h6:{fontWeight:700},button:{fontWeight:700,textTransform:'none'}},components:{MuiButton:{styleOverrides:{root:{borderRadius:10,boxShadow:'none',padding:'9px 16px'},contained:{boxShadow:'0 8px 18px rgba(79,70,229,.18)'}}},MuiPaper:{styleOverrides:{root:{border:'1px solid #e9eaf2',boxShadow:'0 2px 5px rgba(16,24,40,.025)'}}},MuiTextField:{defaultProps:{size:'small'}},MuiOutlinedInput:{styleOverrides:{root:{background:'#fff'}}}}});
const dayShort:Record<string,string>={Monday:'Lun',Tuesday:'Mar',Wednesday:'Mié',Thursday:'Jue',Friday:'Vie'};
const dayFull:Record<string,string>={Monday:'Lunes',Tuesday:'Martes',Wednesday:'Miércoles',Thursday:'Jueves',Friday:'Viernes'};
const slotStart=(s:Slot)=>s.start_time.slice(0,5);
const slotRange=(s:Slot)=>`${s.start_time.slice(0,5)}–${s.end_time.slice(0,5)}`;
const todayIso=()=>new Date().toISOString().slice(0,10);
const pageTitles:Record<string,[string,string]>={Dashboard:['Resumen del día','Una vista rápida de las sustituciones y ausencias.'],Teachers:['Equipo docente','Gestiona las cuentas de acceso del profesorado.'],Availability:['Disponibilidad','Configura guardias y apoyos por sesión.'],Absence:['Comunicar ausencia','Indica los datos necesarios para asignar una sustitución.'],Substitutions:['Sustituciones','Revisa las coberturas que te han asignado.'],History:['Historial','Consulta las ausencias y coberturas registradas.'],Statistics:['Estadísticas','Distribución de sustituciones por docente.']};

function App(){
  const compact=useMediaQuery(theme.breakpoints.down('md'));
  const [token,setToken]=useState(localStorage.token||''),[user,setUser]=useState<User|null>(null),[page,setPage]=useState('Dashboard'),[mobileOpen,setMobileOpen]=useState(false),[teachers,setTeachers]=useState<Teacher[]>([]),[slots,setSlots]=useState<Slot[]>([]),[groups,setGroups]=useState<Group[]>([]),[classrooms,setClassrooms]=useState<Classroom[]>([]);
  const refresh=async(current:User)=>{const [loadedSlots,loadedGroups,loadedClassrooms,loadedTeachers]=await Promise.all([api('/timeslots'),api('/groups'),api('/classrooms'),api('/teachers')]);setSlots(loadedSlots);setGroups(loadedGroups);setClassrooms(loadedClassrooms);setTeachers(loadedTeachers);};
  useEffect(()=>{if(!token)return;api('/auth/me').then((current:User)=>{setUser(current);setPage(current.role==='teacher'?'Substitutions':'Dashboard');return refresh(current)}).catch(()=>{localStorage.removeItem('token');setToken('');setUser(null)})},[token]);
  const logout=()=>{localStorage.removeItem('token');setToken('');setUser(null);setMobileOpen(false)};
  if(!token)return <Login onLogin={x=>{localStorage.token=x;setToken(x)}}/>;
  if(!user)return <Box sx={{minHeight:'100vh',display:'grid',placeItems:'center'}}><CircularProgress/></Box>;
  const admin=user.role==='admin'; const nav:[string,string,React.ReactNode][]=admin?[['Dashboard','Resumen',<DashboardRounded/>],['Teachers','Profesorado',<PeopleAltRounded/>],['Availability','Disponibilidad',<CalendarMonthRounded/>],['Absence','Comunicar ausencia',<AddRounded/>],['Substitutions','Sustituciones',<CheckCircleRounded/>],['History','Historial',<HistoryRounded/>],['Statistics','Estadísticas',<AnalyticsRounded/>]]:[['Absence','Comunicar ausencia',<AddRounded/>],['Substitutions','Sustituciones',<CheckCircleRounded/>],['History','Historial',<HistoryRounded/>],['Statistics','Estadísticas',<AnalyticsRounded/>]];
  const sidebar=<Sidebar admin={admin} nav={nav} page={page} setPage={p=>{setPage(p);setMobileOpen(false)}} logout={logout}/>;
  const [title,subtitle]=pageTitles[page];
  return <Box sx={{minHeight:'100vh',bgcolor:'background.default'}}>{compact?<><AppBar elevation={0} position="sticky" sx={{bgcolor:'#fff',color:'text.primary',borderBottom:'1px solid #e9eaf2'}}><Toolbar><IconButton onClick={()=>setMobileOpen(true)} aria-label="Abrir navegación"><MenuRounded/></IconButton><Box sx={{ml:1.5,flex:1}}><Typography fontWeight={800}>Sustituye</Typography><Typography variant="caption" color="text.secondary">Gestión docente</Typography></Box><Avatar sx={{width:34,height:34,bgcolor:'primary.main',fontSize:14}}>{user.sub[0].toUpperCase()}</Avatar></Toolbar></AppBar><Drawer open={mobileOpen} onClose={()=>setMobileOpen(false)} PaperProps={{sx:{width:drawerWidth,border:0}}}>{sidebar}</Drawer></>:<Drawer variant="permanent" PaperProps={{sx:{width:drawerWidth,border:0,borderRight:'1px solid #e9eaf2'}}}>{sidebar}</Drawer>}<Box component="main" sx={{ml:{md:`${drawerWidth}px`},p:{xs:2,sm:3,lg:4},maxWidth:{xl:1640},mx:'auto'}}><Stack direction="row" alignItems="flex-start" justifyContent="space-between" sx={{mb:3.5}}><Box><Typography variant="h4">{title}</Typography><Typography color="text.secondary" sx={{mt:.65}}>{subtitle}</Typography></Box>{page==='Absence'&&<Chip icon={<ShieldRounded/>} label={admin?'Acceso administrativo':'Solo tu propia ausencia'} color="primary" variant="outlined"/>}</Stack>{page==='Dashboard'&&admin&&<Dashboard teachers={teachers} slots={slots}/>} {page==='Teachers'&&admin&&<Teachers teachers={teachers} reload={()=>refresh(user)}/>} {page==='Availability'&&admin&&<Availability teachers={teachers} slots={slots}/>} {page==='Absence'&&<AbsenceForm teachers={teachers} slots={slots} currentTeacherId={user.teacher_id}/>} {page==='Substitutions'&&<Substitutions admin={admin} teachers={teachers} slots={slots} groups={groups} classrooms={classrooms} currentTeacherId={user.teacher_id}/>} {page==='History'&&<History teachers={teachers} slots={slots} groups={groups} classrooms={classrooms}/>} {page==='Statistics'&&<Stats admin={admin} currentTeacherId={user.teacher_id}/>}</Box></Box>;
}
function Sidebar({admin,nav,page,setPage,logout}:{admin:boolean;nav:[string,string,React.ReactNode][];page:string;setPage:(p:string)=>void;logout:()=>void}){
  return (
    <Box sx={{height:'100%',display:'flex',flexDirection:'column',p:2}}>
      <Stack direction="row" alignItems="center" spacing={1.25} sx={{px:1,py:1.5,mb:3}}>
        <Avatar variant="rounded" sx={{bgcolor:'primary.main',width:40,height:40}}>
          <SchoolRounded/>
        </Avatar>
        <Box>
          <Typography fontWeight={850} letterSpacing="-.03em">Sustituye</Typography>
          <Typography variant="caption" color="text.secondary">Gestión docente</Typography>
        </Box>
      </Stack>

      <Chip
        icon={admin ? <ShieldRounded/> : <PeopleAltRounded/>}
        label={admin ? 'Administración' : 'Portal del profesor'}
        size="small"
        sx={{alignSelf:'flex-start',mx:1,mb:2,bgcolor:admin?'#eef2ff':'#ecfdf5',color:admin?'#4338ca':'#047857'}}
      />

      <List disablePadding>
        {nav.map(([id, label, icon]) => (
          <ListItemButton
            key={id}
            selected={page===id}
            onClick={()=>setPage(id)}
            sx={{
              borderRadius:2.5,
              mb:.5,
              '&.Mui-selected': {
                bgcolor:'#eef2ff',
                color:'primary.main',
                '& .MuiListItemIcon-root': {color:'primary.main'},
              },
              '&:hover': {bgcolor:'#f4f5fb'},
            }}
          >
            <ListItemIcon sx={{minWidth:39,color:page===id?'primary.main':'#667085'}}>{icon}</ListItemIcon>
            <ListItemText primary={label} primaryTypographyProps={{fontWeight:page===id?750:600,fontSize:14}}/>
          </ListItemButton>
        ))}
      </List>

      <Box sx={{mt:'auto',p:1.5,bgcolor:'#f8f8fc',borderRadius:3}}>
        <Typography variant="caption" color="text.secondary">Sesión activa</Typography>
        <Typography variant="body2" fontWeight={700} noWrap>{admin ? 'Administrador' : 'Profesor/a'}</Typography>
        <Button onClick={logout} startIcon={<LogoutRounded/>} size="small" color="inherit" sx={{mt:1,p:0,color:'text.secondary'}}>
          Cerrar sesión
        </Button>
      </Box>
    </Box>
  );
}

function Login({onLogin}:{onLogin:(s:string)=>void}){
  const [email,setEmail]=useState('');
  const [password,setPassword]=useState('');
  const [error,setError]=useState('');
  const [loading,setLoading]=useState(false);

  const submit=async()=>{
    setError('');
    setLoading(true);
    try{
      onLogin((await api('/auth/login',{method:'POST',body:JSON.stringify({email,password})})).access_token)
    }catch(e:any){
      setError(e.message)
    }finally{
      setLoading(false)
    }
  };

  return (
    <Box sx={{minHeight:'100vh',display:'grid',gridTemplateColumns:{md:'1.1fr .9fr'},bgcolor:'#f7f7fc'}}>
      <Box sx={{display:{xs:'none',md:'flex'},flexDirection:'column',justifyContent:'space-between',p:7,color:'#fff',background:'radial-gradient(circle at 15% 25%,#7c73ff 0,#4f46e5 38%,#28225f 100%)'}}>
        <Stack direction="row" spacing={1.25} alignItems="center">
          <Avatar variant="rounded" sx={{bgcolor:'rgba(255,255,255,.18)'}}>
            <SchoolRounded/>
          </Avatar>
          <Typography fontWeight={800} fontSize={20}>Sustituye</Typography>
        </Stack>

        <Box>
          <Chip label="GESTIÓN DOCENTE" sx={{bgcolor:'rgba(255,255,255,.14)',color:'#fff',fontWeight:800,fontSize:11,letterSpacing:1}}/>
          <Typography variant="h3" sx={{mt:2.5,fontWeight:800,maxWidth:520,lineHeight:1.06}}>Organiza las ausencias con claridad.</Typography>
          <Typography sx={{mt:2,maxWidth:420,opacity:.78,fontSize:17}}>Un espacio sencillo para coordinar al equipo docente y las sustituciones.</Typography>
        </Box>

        <Typography sx={{opacity:.65,fontSize:13}}>Acceso seguro para administración y profesorado</Typography>
      </Box>

      <Box sx={{display:'grid',placeItems:'center',p:{xs:3,sm:6}}}>
        <Paper sx={{width:'100%',maxWidth:420,p:{xs:3,sm:4},borderRadius:4,boxShadow:'0 20px 55px rgba(31,36,80,.1)'}}>
          <Avatar variant="rounded" sx={{bgcolor:'#eef2ff',color:'primary.main',mb:2}}>
            <ShieldRounded/>
          </Avatar>
          <Typography variant="h5">Bienvenido/a</Typography>
          <Typography color="text.secondary" sx={{mt:.75,mb:3}}>Introduce tus credenciales para continuar.</Typography>
          <Stack spacing={2}>
            <TextField label="Correo electrónico" type="email" autoComplete="email" value={email} onChange={e=>setEmail(e.target.value)} onKeyDown={e=>e.key==='Enter'&&submit()}/>
            <TextField label="Contraseña" type="password" autoComplete="current-password" value={password} onChange={e=>setPassword(e.target.value)} onKeyDown={e=>e.key==='Enter'&&submit()}/>
            {error&&<Alert severity="error">{error}</Alert>}
            <Button size="large" variant="contained" endIcon={loading ? <CircularProgress size={17} color="inherit"/> : <ArrowForwardRounded/>} disabled={loading||!email||!password} onClick={submit}>
              Acceder
            </Button>
          </Stack>
        </Paper>
      </Box>
    </Box>
  );
}
function StatCard({label,value,detail,tone,icon}:{label:string;value:number;detail:string;tone:'indigo'|'green'|'amber';icon:React.ReactNode}){const colors={indigo:['#eef2ff','#4f46e5'],green:['#ecfdf3','#15803d'],amber:['#fff7ed','#d97706']}[tone];return <Paper sx={{p:2.5,borderRadius:3}}><Stack direction="row" justifyContent="space-between"><Box><Typography variant="body2" color="text.secondary" fontWeight={650}>{label}</Typography><Typography variant="h4" sx={{mt:.75}}>{value}</Typography></Box><Avatar variant="rounded" sx={{bgcolor:colors[0],color:colors[1],width:46,height:46}}>{icon}</Avatar></Stack><Typography variant="caption" color="text.secondary" sx={{display:'block',mt:1.5}}>{detail}</Typography></Paper>}
function Dashboard({teachers,slots}:{teachers:Teacher[];slots:Slot[]}){const [data,setData]=useState<any>();const reload=()=>api('/dashboard').then(setData);useEffect(()=>{reload()},[]);if(!data)return <Loading/>;const slot=(id:number)=>slots.find(x=>x.id===id);return <Stack spacing={3}><Box sx={{display:'grid',gridTemplateColumns:{xs:'1fr',sm:'repeat(3,1fr)'},gap:2}}><StatCard label="Ausencias hoy" value={data.absence_count} detail="Comunicadas para la jornada actual" tone="indigo" icon={<DescriptionRounded/>}/><StatCard label="Con sustitución" value={data.covering_count} detail="Coberturas asignadas" tone="green" icon={<CheckCircleRounded/>}/><StatCard label="Pendientes" value={data.unassigned_count} detail="Requieren revisión" tone="amber" icon={<WarningAmberRounded/>}/></Box><SyncEducaMadridButton onImported={reload}/><Paper sx={{p:{xs:2,sm:3},borderRadius:3}}><Stack direction="row" justifyContent="space-between" alignItems="center" sx={{mb:2.5}}><Box><Typography variant="h6">Sustituciones de hoy</Typography><Typography variant="body2" color="text.secondary">Estado actualizado de la jornada</Typography></Box><Chip label={new Date().toLocaleDateString('es-ES',{day:'numeric',month:'short'})} size="small"/></Stack>{data.substitutions.length===0?<Empty icon={<CalendarMonthRounded/>} text="No hay ausencias registradas para hoy."/>:<Stack divider={<Divider flexItem/>}>{data.substitutions.map((a:Absence)=><Stack key={a.id} direction="row" alignItems="center" justifyContent="space-between" sx={{py:1.25}}><Stack direction="row" spacing={1.5} alignItems="center"><Avatar sx={{bgcolor:'#eef2ff',color:'primary.main',width:36,height:36,fontSize:12,fontWeight:800}}>{slot(a.timeslot_id)?slotStart(slot(a.timeslot_id)!):'—'}</Avatar><Box><Typography fontWeight={700}>{slot(a.timeslot_id)?`${dayShort[slot(a.timeslot_id)!.weekday]} · ${slotRange(slot(a.timeslot_id)!)}`:'Sesión sin datos'}</Typography><Typography variant="caption" color="text.secondary">Ausencia comunicada</Typography></Box></Stack><Chip size="small" color={a.substitute_teacher_id?'success':'warning'} label={a.substitute_teacher_id?'Cubierta':'Pendiente'}/></Stack>)}</Stack>}</Paper></Stack>}
function SyncEducaMadridButton({onImported}:{onImported:()=>void}){
  const [loading,setLoading]=useState(false);
  const [result,setResult]=useState<any>();
  const [error,setError]=useState('');
  const run=async()=>{
    try{
      setLoading(true);setError('');setResult(undefined);
      const data=await api('/admin/sync-educamadrid',{method:'POST'});
      setResult(data);
      if(data.imported>0) onImported();
    }catch(e:any){setError(e.message)}finally{setLoading(false)}
  };
  return <Paper sx={{p:{xs:2,sm:3},borderRadius:3}}>
    <Stack direction={{xs:'column',sm:'row'}} spacing={1.5} alignItems={{sm:'center'}} justifyContent="space-between">
      <Stack direction="row" spacing={1.5} alignItems="center">
        <Avatar variant="rounded" sx={{bgcolor:'#eef2ff',color:'primary.main'}}><SyncRounded/></Avatar>
        <Box><Typography variant="h6">Sincronizar EducaMadrid</Typography><Typography variant="body2" color="text.secondary">Descarga las respuestas nuevas del formulario y las añade como sustituciones.</Typography></Box>
      </Stack>
      <Button variant="contained" onClick={run} disabled={loading} startIcon={loading?<CircularProgress size={16} color="inherit"/>:<SyncRounded/>}>{loading?'Sincronizando…':'Sincronizar ahora'}</Button>
    </Stack>
    {error&&<Alert severity="error" sx={{mt:2}} onClose={()=>setError('')}>{error}</Alert>}
    {result&&<Alert severity={result.pending>0?'warning':'success'} sx={{mt:2}} onClose={()=>setResult(undefined)}>
      {`Se han descargado ${result.fetched} respuestas (${result.complete} completadas). Se importaron ${result.imported} sustituciones nuevas.`}{result.pending>0?` ${result.pending} se quedaron pendientes de revisión.`:''}
    </Alert>}
  </Paper>;
}
function Teachers({teachers,reload}:{teachers:Teacher[];reload:()=>void}){const empty={first_name:'',last_name:'',email:'',password:'',active:true};const [form,setForm]=useState(empty),[reset,setReset]=useState<Record<number,string>>({}),[notice,setNotice]=useState(''),[error,setError]=useState('');const save=async()=>{try{setError('');await api('/teachers',{method:'POST',body:JSON.stringify(form)});setForm(empty);setNotice('Profesor añadido correctamente.');reload()}catch(e:any){setError(e.message)}};const changePassword=async(t:Teacher)=>{try{await api('/teachers/'+t.id,{method:'PUT',body:JSON.stringify({...t,password:reset[t.id]})});setReset({...reset,[t.id]:''});setNotice(`Contraseña actualizada para ${t.first_name}.`)}catch(e:any){setError(e.message)}};const deleteTeacher=async(id:number)=>{try{setError('');setNotice('');await api('/teachers/'+id,{method:'DELETE'});setNotice('Profesor eliminado correctamente.');reload()}catch(e:any){setError(e.message)}};return <Stack spacing={3}><Paper sx={{p:{xs:2,sm:3},borderRadius:3}}><Stack direction={{xs:'column',sm:'row'}} justifyContent="space-between" spacing={1} sx={{mb:2.5}}><Box><Typography variant="h6">Añadir profesor</Typography><Typography variant="body2" color="text.secondary">La contraseña debe tener al menos 8 caracteres.</Typography></Box><Chip label={`${teachers.length} cuentas`} color="primary" variant="outlined"/></Stack><Box sx={{display:'grid',gridTemplateColumns:{xs:'1fr',sm:'repeat(2,1fr)',lg:'1fr 1fr 1.3fr 1.1fr auto'},gap:1.25,alignItems:'center'}}><TextField label="Nombre" value={form.first_name} onChange={e=>setForm({...form,first_name:e.target.value})}/><TextField label="Apellidos" value={form.last_name} onChange={e=>setForm({...form,last_name:e.target.value})}/><TextField label="Correo electrónico" value={form.email} onChange={e=>setForm({...form,email:e.target.value})}/><TextField label="Contraseña" type="password" value={form.password} onChange={e=>setForm({...form,password:e.target.value})}/><Button variant="contained" startIcon={<AddRounded/>} onClick={save} disabled={!form.first_name||!form.last_name||!form.email||form.password.length<8}>Añadir</Button></Box>{(notice||error)&&<Alert severity={error?'error':'success'} sx={{mt:2}} onClose={()=>{setError('');setNotice('')}}>{error||notice}</Alert>}</Paper><Paper sx={{borderRadius:3,overflow:'hidden'}}><Box sx={{p:2.5,borderBottom:'1px solid #eceef4'}}><Typography variant="h6">Cuentas del profesorado</Typography></Box>{teachers.length===0?<Empty icon={<PeopleAltRounded/>} text="Aún no se han creado cuentas de profesor."/>:<Stack divider={<Divider flexItem/>}>{teachers.map(t=><Box key={t.id} sx={{p:{xs:2,sm:2.5}}}><Stack direction={{xs:'column',md:'row'}} spacing={1.5} alignItems={{md:'center'}} justifyContent="space-between"><Stack direction="row" spacing={1.5} alignItems="center"><Avatar sx={{bgcolor:'#eef2ff',color:'primary.main',fontWeight:800}}>{t.first_name[0]}{t.last_name[0]}</Avatar><Box><Typography fontWeight={750}>{t.first_name} {t.last_name}</Typography><Typography variant="body2" color="text.secondary">{t.email}</Typography></Box></Stack><Stack direction={{xs:'column',sm:'row'}} spacing={1}><TextField size="small" label="Nueva contraseña" type="password" value={reset[t.id]||''} onChange={e=>setReset({...reset,[t.id]:e.target.value})}/><Button variant="outlined" onClick={()=>changePassword(t)} disabled={(reset[t.id]||'').length<8}>Actualizar</Button><Tooltip title="Eliminar profesor"><IconButton color="error" onClick={()=>deleteTeacher(t.id)}><CloseRounded/></IconButton></Tooltip></Stack></Stack></Box>)}</Stack>}</Paper></Stack>}
function Availability({teachers,slots}:{teachers:Teacher[];slots:Slot[]}){const [rows,setRows]=useState<any[]>([]);useEffect(()=>{api('/availability').then(setRows)},[]);const selected=(id:number)=>rows.filter(x=>x.teacher_id===id);const save=async(t:number,s:number,type:string)=>{const entries=selected(t).filter(x=>x.timeslot_id!==s).concat(type?[{timeslot_id:s,duty_type:type}]:[]).map(x=>({timeslot_id:x.timeslot_id,duty_type:x.duty_type}));await api('/availability',{method:'PUT',body:JSON.stringify({teacher_id:t,entries})});setRows(await api('/availability'))};return <Paper sx={{borderRadius:3,overflow:'hidden'}}><Box sx={{p:2.5,borderBottom:'1px solid #eceef4'}}><Typography variant="h6">Planificación semanal</Typography><Stack direction="row" spacing={1} sx={{mt:1}}><Chip label="G · Guardia" size="small" color="primary" variant="outlined"/><Chip label="A · Apoyo" size="small" color="secondary" variant="outlined"/></Stack></Box><Box sx={{overflowX:'auto'}}><Box component="table" sx={{width:'100%',borderCollapse:'collapse',minWidth:980,'th':{p:1.25,bgcolor:'#f8f9fc',fontSize:12,color:'text.secondary',fontWeight:800,whiteSpace:'nowrap',borderBottom:'1px solid #e9eaf2'},'td':{p:1,borderBottom:'1px solid #f0f1f5',textAlign:'center'},'td:first-of-type':{textAlign:'left',fontWeight:700,whiteSpace:'nowrap'}}}><thead><tr><th style={{textAlign:'left'}}>Profesor/a</th>{slots.map(s=><th key={s.id}>{dayShort[s.weekday]}<br/>{slotStart(s)}</th>)}</tr></thead><tbody>{teachers.map(t=><tr key={t.id}><td>{t.first_name} {t.last_name}</td>{slots.map(s=>{const value=selected(t.id).find(x=>x.timeslot_id===s.id)?.duty_type||'';return <td key={s.id}><select aria-label={`${t.last_name} ${s.weekday} sesión ${slotRange(s)}`} value={value} onChange={e=>save(t.id,s.id,e.target.value)} style={{border:'1px solid #e1e5ee',borderRadius:7,padding:'5px',background:'#fff',fontWeight:700,color:value==='GUARD'?'#4338ca':value==='SUPPORT'?'#0f766e':'#98a2b3'}}><option value="">—</option><option value="GUARD">G</option><option value="SUPPORT">A</option></select></td>})}</tr>)}</tbody></Box></Box></Paper>}
function AbsenceForm({teachers,slots,currentTeacherId}:{teachers:Teacher[];slots:Slot[];currentTeacherId?:number}){const [form,setForm]=useState<any>({date:new Date().toISOString().slice(0,10),timeslot_id:'',absent_teacher_id:currentTeacherId||'',class_group:'',classroom:'',task_left:'',observations:''}),[result,setResult]=useState<any>(),[error,setError]=useState('');useEffect(()=>{if(currentTeacherId)setForm((v:any)=>({...v,absent_teacher_id:currentTeacherId}))},[currentTeacherId]);const set=(key:string,value:any)=>setForm({...form,[key]:value});const renderGroupedSlots=()=>{const items:JSX.Element[]=[];slots.forEach((s,i)=>{if(i===0||slots[i-1].weekday!==s.weekday){items.push(<ListSubheader key={`header-${s.weekday}-${s.id}`}>{dayFull[s.weekday]||s.weekday}</ListSubheader>);}items.push(<MenuItem key={s.id} value={s.id}>{slotRange(s)}</MenuItem>);});return items;};const groupedSlots=(<FormControl fullWidth><InputLabel>Sesión afectada</InputLabel><Select label="Sesión afectada" value={form.timeslot_id} onChange={e=>set('timeslot_id',e.target.value)}>{renderGroupedSlots()}</Select></FormControl>);const submit=async()=>{try{setError('');setResult(await api('/absences',{method:'POST',body:JSON.stringify(form)}))}catch(e:any){setError(e.message)}};return <Box sx={{maxWidth:880}}><Paper sx={{p:{xs:2,sm:3.5},borderRadius:3}}><Stack direction="row" spacing={1.5} alignItems="center" sx={{mb:3}}><Avatar variant="rounded" sx={{bgcolor:'#eef2ff',color:'primary.main'}}><DescriptionRounded/></Avatar><Box><Typography variant="h6">Datos de la ausencia</Typography><Typography variant="body2" color="text.secondary">La sustitución se asignará automáticamente al guardar.</Typography></Box></Stack><Stack spacing={2.25}><Box sx={{display:'grid',gridTemplateColumns:{xs:'1fr',sm:'1fr 1fr'},gap:2}}><TextField fullWidth label="Fecha" type="date" value={form.date} onChange={e=>set('date',e.target.value)} slotProps={{inputLabel:{shrink:true}}}/>{groupedSlots}</Box>{!currentTeacherId&&<FormControl fullWidth><InputLabel>Profesor/a ausente</InputLabel><Select label="Profesor/a ausente" value={form.absent_teacher_id} onChange={e=>set('absent_teacher_id',e.target.value)}>{teachers.map(t=><MenuItem key={t.id} value={t.id}>{t.first_name} {t.last_name}</MenuItem>)}</Select></FormControl>}<Box sx={{display:'grid',gridTemplateColumns:{xs:'1fr',sm:'1fr 1fr'},gap:2}}><TextField fullWidth required label="Grupo" placeholder="Ej. 3º ESO B" value={form.class_group} onChange={e=>set('class_group',e.target.value)}/><TextField fullWidth required label="Aula" placeholder="Ej. B-204" value={form.classroom} onChange={e=>set('classroom',e.target.value)}/></Box><TextField fullWidth required label="Tarea o instrucciones" placeholder="Indica el trabajo que debe realizar el grupo" multiline minRows={3} value={form.task_left} onChange={e=>set('task_left',e.target.value)}/><TextField fullWidth label="Observaciones" placeholder="Información adicional para la persona sustituta" multiline minRows={2} value={form.observations} onChange={e=>set('observations',e.target.value)}/>{error&&<Alert severity="error">{error}</Alert>}{result&&<Alert severity={result.substitute_teacher_id?'success':'warning'} icon={result.substitute_teacher_id?<CheckCircleRounded/>:<WarningAmberRounded/>}>{result.substitute_teacher_id?'Ausencia registrada y sustitución asignada correctamente.':'Ausencia registrada, pero todavía no hay una persona disponible para cubrirla.'}</Alert>}<Box sx={{display:'flex',justifyContent:'flex-end'}}><Button size="large" variant="contained" startIcon={<CheckCircleRounded/>} onClick={submit} disabled={!form.timeslot_id||!form.absent_teacher_id||!form.class_group.trim()||!form.classroom.trim()||!form.task_left.trim()}>Registrar ausencia</Button></Box></Stack></Paper></Box>}

function Substitutions({admin,teachers,slots,groups,classrooms,currentTeacherId}:{admin:boolean;teachers:Teacher[];slots:Slot[];groups:Group[];classrooms:Classroom[];currentTeacherId?:number}){const [rows,setRows]=useState<Absence[]>([]);const [notice,setNotice]=useState('');const [error,setError]=useState('');useEffect(()=>{api('/absences?date_from='+todayIso()).then(setRows)},[]);const refreshRows=async()=>setRows(await api('/absences?date_from='+todayIso()));const visible=admin?rows:rows.filter(r=>r.substitute_teacher_id===currentTeacherId);const teacherName=(id?:number|null)=>teachers.find(t=>t.id===id)?.first_name+' '+teachers.find(t=>t.id===id)?.last_name||'Profesor/a';const groupName=(id:number)=>groups.find(x=>x.id===id)?.name||'Grupo';const classroomName=(id:number)=>classrooms.find(x=>x.id===id)?.name||'Aula';const deleteAbsence=async(id:number)=>{try{setError('');setNotice('');await api('/absences/'+id,{method:'DELETE'});await refreshRows();setNotice('Sustitución eliminada correctamente.')}catch(e:any){setError(e.message)}};return <Paper sx={{borderRadius:3,overflow:'hidden'}}><Box sx={{p:2.5,borderBottom:'1px solid #eceef4'}}><Typography variant="h6">Sustituciones asignadas</Typography><Typography variant="body2" color="text.secondary">{admin?'Todas las coberturas registradas en el centro.':'Tus sustituciones pendientes y realizadas.'}</Typography></Box>{(notice||error)&&<Box sx={{p:2.5}}><Alert severity={error?'error':'success'} onClose={()=>{setError('');setNotice('')}}>{error||notice}</Alert></Box>}{visible.length===0?<Empty icon={<CheckCircleRounded/>} text={admin?'Aún no hay sustituciones asignadas.':'No tienes sustituciones asignadas.'}/>:<Stack divider={<Divider flexItem/>}>{visible.map(a=>{const slot=slots.find(s=>s.id===a.timeslot_id);const substitute=teacherName(a.substitute_teacher_id);const absent=admin?teacherName(a.absent_teacher_id):'';return <Box key={a.id} sx={{p:{xs:2,sm:2.5}}}><Stack direction={{xs:'column',md:'row'}} spacing={1.5} alignItems={{md:'center'}} justifyContent="space-between"><Box sx={{flex:1}}><Stack direction="row" spacing={1} sx={{mb:1}}><Chip size="small" color={a.substitute_teacher_id===currentTeacherId&&!admin?'success':'primary'} label={slot?`${dayShort[slot.weekday]} · ${slotRange(slot)}`:'Sesión'}/>{admin&&<Chip size="small" variant="outlined" label={`Sustituye: ${substitute}`}/>}</Stack><Typography fontWeight={750}>{groupName(a.class_group_id)} · {classroomName(a.classroom_id)}</Typography><Typography variant="body2" color="text.secondary">{new Date(a.date+'T00:00:00').toLocaleDateString('es-ES')} · {a.task_left}</Typography>{a.observations&&<Typography variant="caption" color="text.secondary">{a.observations}</Typography>}{admin&&<Typography variant="caption" color="text.secondary" sx={{display:'block',mt:.5}}>Ausencia de {absent}</Typography>}</Box><Stack direction="row" spacing={1} alignItems="center"><Chip size="small" color={a.substitute_teacher_id?'success':'warning'} label={a.substitute_teacher_id?'Asignada':'Pendiente'}/>{admin&&<Button size="small" color="error" variant="outlined" onClick={()=>deleteAbsence(a.id)}>Eliminar</Button>}</Stack></Stack></Box>})}</Stack>}</Paper>}
function History({teachers,slots,groups,classrooms}:{teachers:Teacher[];slots:Slot[];groups:Group[];classrooms:Classroom[]}){const [rows,setRows]=useState<Absence[]>([]);useEffect(()=>{api('/absences').then(setRows)},[]);const teacherName=(id?:number|null)=>teachers.find(t=>t.id===id)?.first_name+' '+teachers.find(t=>t.id===id)?.last_name||'Profesor/a';const groupName=(id:number)=>groups.find(x=>x.id===id)?.name||'Grupo';const classroomName=(id:number)=>classrooms.find(x=>x.id===id)?.name||'Aula';return <Paper sx={{borderRadius:3,overflow:'hidden'}}><Box sx={{p:2.5,borderBottom:'1px solid #eceef4'}}><Typography variant="h6">Últimos registros</Typography><Typography variant="body2" color="text.secondary">Ausencias, coberturas y quién las realiza.</Typography></Box>{rows.length===0?<Empty icon={<HistoryRounded/>} text="Aún no hay ausencias registradas."/>:<Stack divider={<Divider flexItem/>}>{rows.map(a=>{const teacher=teacherName(a.absent_teacher_id);const slot=slots.find(s=>s.id===a.timeslot_id);const substitute=a.substitute_teacher_id?teacherName(a.substitute_teacher_id):'Pendiente';return <Stack key={a.id} direction="row" justifyContent="space-between" alignItems="center" sx={{p:2.25}}><Box><Typography fontWeight={750}>{teacher}</Typography><Typography variant="body2" color="text.secondary">{new Date(a.date+'T00:00:00').toLocaleDateString('es-ES')} · {slot?`${dayShort[slot.weekday]} · ${slotRange(slot)}`:'Sesión'} · {groupName(a.class_group_id)} · {classroomName(a.classroom_id)}</Typography><Typography variant="caption" color="text.secondary">Sustituye: {substitute}</Typography></Box><Chip color={a.substitute_teacher_id?'success':'warning'} size="small" label={a.substitute_teacher_id?'Cubierta':'Pendiente'}/></Stack>})}</Stack>}</Paper>}
function Stats({admin,currentTeacherId}:{admin:boolean;currentTeacherId?:number}){
  const [rows,setRows]=useState<any[]>([]);useEffect(()=>{api('/statistics').then(setRows)},[]);
  const maximum=Math.max(...rows.map(x=>x.total),1);
  const own=rows.find(x=>x.teacher.id===currentTeacherId);
  return admin
    ? <Stack spacing={3}>
        <Paper sx={{p:{xs:2,sm:3},borderRadius:3,maxWidth:900}}><Typography variant="h6">Carga de sustituciones</Typography><Typography variant="body2" color="text.secondary" sx={{mb:3}}>Total acumulado por profesor/a.</Typography>{rows.length===0?<Empty icon={<AnalyticsRounded/>} text="Aún no hay datos estadísticos."/>:<Stack spacing={2}>{rows.map(x=><Box key={x.teacher.id}><Stack direction="row" justifyContent="space-between" sx={{mb:.65}}><Typography fontWeight={700}>{x.teacher.name}</Typography><Typography color="text.secondary" variant="body2">{x.total} asignaciones</Typography></Stack><Box sx={{height:8,bgcolor:'#eef0f6',borderRadius:99,overflow:'hidden'}}><Box sx={{height:'100%',width:`${(x.total/maximum)*100}%`,bgcolor:'primary.main',borderRadius:99}}/></Box></Box>)}</Stack>}</Paper>
        <DatabaseBackup/>
        <ResetYearData/>
      </Stack>
    : <Box sx={{maxWidth:340}}><StatCard label="Sustituciones realizadas" value={own?.total||0} detail="Total de coberturas que has hecho" tone="indigo" icon={<AnalyticsRounded/>}/></Box>;
}
function DatabaseBackup(){
  const fileInputRef=React.useRef<HTMLInputElement>(null);
  const [pendingFile,setPendingFile]=useState<File|null>(null);
  const [confirmOpen,setConfirmOpen]=useState(false);
  const [downloading,setDownloading]=useState(false);
  const [restoring,setRestoring]=useState(false);
  const [notice,setNotice]=useState('');
  const [error,setError]=useState('');

  const download=async()=>{
    try{
      setError('');setNotice('');setDownloading(true);
      await apiDownload('/backup/export',`backup-${todayIso()}.json`);
      setNotice('Copia de seguridad descargada correctamente.');
    }catch(e:any){setError(e.message)}finally{setDownloading(false)}
  };

  const pickFile=()=>fileInputRef.current?.click();
  const onFileSelected=(e:React.ChangeEvent<HTMLInputElement>)=>{
    const file=e.target.files?.[0];
    e.target.value='';
    if(!file)return;
    setPendingFile(file);
    setConfirmOpen(true);
  };
  const closeConfirm=()=>{if(restoring)return;setConfirmOpen(false);setPendingFile(null)};
  const confirmRestore=async()=>{
    if(!pendingFile)return;
    try{
      setError('');setNotice('');setRestoring(true);
      await apiUpload('/backup/import',pendingFile);
      setNotice('Base de datos restaurada correctamente. Es posible que necesites recargar la página.');
      setConfirmOpen(false);setPendingFile(null);
    }catch(e:any){setError(e.message)}finally{setRestoring(false)}
  };

  return <Paper sx={{p:{xs:2,sm:3},borderRadius:3,maxWidth:900}}>
    <Stack direction="row" spacing={1.5} alignItems="center">
      <Avatar variant="rounded" sx={{bgcolor:'#eef2ff',color:'primary.main'}}><StorageRounded/></Avatar>
      <Box><Typography variant="h6">Gestión de base de datos</Typography><Typography variant="body2" color="text.secondary">Descarga una copia de seguridad completa o restaura los datos desde un archivo JSON.</Typography></Box>
    </Stack>
    {(notice||error)&&<Alert severity={error?'error':'success'} sx={{mt:2}} onClose={()=>{setError('');setNotice('')}}>{error||notice}</Alert>}
    <Stack direction={{xs:'column',sm:'row'}} spacing={1.5} sx={{mt:2.5}}>
      <Button variant="contained" onClick={download} disabled={downloading} startIcon={downloading?<CircularProgress size={16} color="inherit"/>:<DownloadRounded/>}>
        {downloading?'Descargando…':'Descargar copia de seguridad'}
      </Button>
      <Button variant="outlined" color="error" onClick={pickFile} disabled={restoring} startIcon={<UploadRounded/>}>
        Restaurar base de datos
      </Button>
      <input ref={fileInputRef} type="file" accept="application/json,.json" hidden onChange={onFileSelected}/>
    </Stack>

    <Dialog open={confirmOpen} onClose={closeConfirm} maxWidth="xs" fullWidth>
      <DialogTitle>¿Restaurar base de datos?</DialogTitle>
      <DialogContent>
        <DialogContentText>Vas a restaurar la base de datos desde <strong>{pendingFile?.name}</strong>.</DialogContentText>
        <DialogContentText sx={{mt:1.5,color:'error.main',fontWeight:700}}>
          Esta acción es irreversible y borrará todos los datos actuales.
        </DialogContentText>
        {error&&<Alert severity="error" sx={{mt:2}}>{error}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={closeConfirm} disabled={restoring}>Cancelar</Button>
        <Button color="error" variant="contained" onClick={confirmRestore} disabled={restoring} startIcon={restoring?<CircularProgress size={16} color="inherit"/>:<UploadRounded/>}>
          {restoring?'Restaurando…':'Restaurar definitivamente'}
        </Button>
      </DialogActions>
    </Dialog>
  </Paper>;
}
function ResetYearData(){
  const CONFIRM_WORD='ELIMINAR';
  const [open,setOpen]=useState(false);
  const [step,setStep]=useState(1);
  const [confirmText,setConfirmText]=useState('');
  const [loading,setLoading]=useState(false);
  const [notice,setNotice]=useState('');
  const [error,setError]=useState('');
  const close=()=>{setOpen(false);setStep(1);setConfirmText('');setError('')};
  const confirm=async()=>{
    try{
      setLoading(true);setError('');
      const result=await api('/admin/reset-year',{method:'POST'});
      setNotice(`Se han eliminado ${result.absences_deleted} sustituciones y ${result.statistics_deleted} registros de estadísticas. El profesorado, su disponibilidad, los grupos y las aulas se han mantenido.`);
      close();
    }catch(e:any){setError(e.message)}finally{setLoading(false)}
  };
  return <Paper sx={{p:{xs:2,sm:3},borderRadius:3,maxWidth:900,border:'1px solid #fecdd3'}}>
    <Stack direction="row" spacing={1.5} alignItems="center">
      <Avatar variant="rounded" sx={{bgcolor:'#fef2f2',color:'#dc2626'}}><DeleteForeverRounded/></Avatar>
      <Box><Typography variant="h6">Empezar nuevo curso académico</Typography><Typography variant="body2" color="text.secondary">Elimina permanentemente las sustituciones registradas y las estadísticas de asignación. El profesorado, su disponibilidad, los grupos y las aulas configuradas se mantienen.</Typography></Box>
    </Stack>
    {notice&&<Alert severity="success" sx={{mt:2}} onClose={()=>setNotice('')}>{notice}</Alert>}
    <Box sx={{display:'flex',justifyContent:'flex-end',mt:2}}>
      <Button color="error" variant="outlined" startIcon={<DeleteForeverRounded/>} onClick={()=>setOpen(true)}>Eliminar sustituciones y estadísticas</Button>
    </Box>
    <Dialog open={open} onClose={close} maxWidth="xs" fullWidth>
      {step===1?<>
        <DialogTitle>¿Eliminar los datos del curso?</DialogTitle>
        <DialogContent><DialogContentText>Esta acción eliminará permanentemente todas las sustituciones registradas y las estadísticas de asignación de todo el centro. No se puede deshacer. El profesorado, su disponibilidad, los grupos y las aulas configuradas no se verán afectados.</DialogContentText></DialogContent>
        <DialogActions><Button onClick={close}>Cancelar</Button><Button color="error" variant="contained" onClick={()=>setStep(2)}>Continuar</Button></DialogActions>
      </>:<>
        <DialogTitle>Confirmación final</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{mb:2}}>Para confirmar, escribe <strong>{CONFIRM_WORD}</strong> en el siguiente campo.</DialogContentText>
          <TextField fullWidth autoFocus value={confirmText} onChange={e=>setConfirmText(e.target.value)} placeholder={CONFIRM_WORD}/>
          {error&&<Alert severity="error" sx={{mt:2}}>{error}</Alert>}
        </DialogContent>
        <DialogActions>
          <Button onClick={close} disabled={loading}>Cancelar</Button>
          <Button color="error" variant="contained" disabled={confirmText!==CONFIRM_WORD||loading} onClick={confirm} startIcon={loading?<CircularProgress size={16} color="inherit"/>:<DeleteForeverRounded/>}>Eliminar definitivamente</Button>
        </DialogActions>
      </>}
    </Dialog>
  </Paper>;
}
function Empty({icon,text}:{icon:React.ReactNode;text:string}){return <Stack alignItems="center" spacing={1.25} sx={{py:6,color:'text.secondary'}}><Avatar sx={{bgcolor:'#f0f1ff',color:'primary.main',width:46,height:46}}>{icon}</Avatar><Typography variant="body2">{text}</Typography></Stack>}
function Loading(){return <Paper sx={{p:5,borderRadius:3,display:'grid',placeItems:'center'}}><CircularProgress/></Paper>}
createRoot(document.getElementById('root')!).render(<ThemeProvider theme={theme}><CssBaseline/><App/></ThemeProvider>);
