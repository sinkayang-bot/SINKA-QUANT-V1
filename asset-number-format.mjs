// Display-only decimal rounding. Never convert an account amount to Number.
export function formatAssetAmount(value,{places=8,minPlaces=0,fullPrecision=false}={}) {
  if(typeof value!=='string' || value.length>96 || !/^-?\d+(?:\.\d+)?$/.test(value))return '未提供';
  if(fullPrecision)return value;
  if(!Number.isInteger(places) || places<0 || places>24 || !Number.isInteger(minPlaces) || minPlaces<0 || minPlaces>places)throw new RangeError('Invalid display precision');
  const negative=value.startsWith('-');
  const [integer,fraction='']=value.replace(/^-/,'').split('.');
  const discarded=fraction.slice(places), approximate=/[1-9]/.test(discarded);
  let scaled=BigInt(integer+fraction.slice(0,places).padEnd(places,'0'));
  if(discarded && discarded[0]>='5')scaled+=1n;
  const digits=scaled.toString().padStart(places+1,'0');
  const whole=places?digits.slice(0,-places):digits;
  let decimals=places?digits.slice(-places):'';
  while(decimals.length>minPlaces && decimals.endsWith('0'))decimals=decimals.slice(0,-1);
  const sign=negative && (scaled!==0n || approximate)?'-':'';
  return (approximate?'≈ ':'')+sign+whole.replace(/\B(?=(\d{3})+(?!\d))/g,',')+(decimals?'.'+decimals:'');
}
