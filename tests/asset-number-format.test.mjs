import test from 'node:test';
import assert from 'node:assert/strict';
import {formatAssetAmount as format} from '../asset-number-format.mjs';
const usd={places:2,minPlaces:2};
test('USD presentation groups thousands and keeps two decimal places',()=>{
  assert.equal(format('123456.7',usd),'123,456.70');
  assert.equal(format('1000.0000',usd),'1,000.00');
});
test('decimal rounding preserves signs and carries without floating-point error',()=>{
  assert.equal(format('1.005',usd),'≈ 1.01');assert.equal(format('-1.005',usd),'≈ -1.01');
  assert.equal(format('999.999',usd),'≈ 1,000.00');
});
test('tiny nonzero amounts are explicitly approximate and exact source can be displayed',()=>{
  assert.equal(format('-0.000000001'),'≈ -0');
  assert.equal(format('-0.000000001',{fullPrecision:true}),'-0.000000001');
  assert.equal(format('0.00000001'),'0.00000001');
});
test('very large decimal amounts never lose significant digits through Number conversion',()=>{
  assert.equal(format('900719925474099312345.6789',usd),'≈ 900,719,925,474,099,312,345.68');
  assert.equal(format('900719925474099312345.6789',{fullPrecision:true}),'900719925474099312345.6789');
});
test('absent and malformed numbers are never shown as zero or raw markup',()=>{
  for(const value of [null,undefined,'NaN','<img onerror=x>',123])assert.equal(format(value),'未提供');
  assert.equal(format('0',usd),'0.00');
});
