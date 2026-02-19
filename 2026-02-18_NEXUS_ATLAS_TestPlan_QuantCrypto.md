# NEXUS / ATLAS - Quant Crypto R&D Sprint Test Plan (7 Days)

**Muc tieu:** Sau khi NEXUS hoan thien, chay bai test nay de danh gia nang luc tu chu R&D end-to-end va chat luong ket qua theo tieu chi dinh luong.

## 1) Set "tinh cach van hanh" (0.5 ngay)
- Dinh nghia mission: tu chu end-to-end, do luong duoc, uu tien tinh dung va tai lap.
- Quy tac lam viec: hoai nghi du lieu, ghi log quyet dinh, pivot theo bang chung, khong bia ket qua.
- Hop dong dau ra: moi ngay co update + decision log + ket qua thi nghiem; cuoi sprint co repo chay duoc + bao cao.

## 2) Khoi tao chuan danh gia (0.5 ngay)
- Chot benchmark (vi du buy&hold, hoac index, hoac strategy baseline) + khung thoi gian + tan suat (1h/4h/1d).
- Chot gia dinh chi phi: fees, slippage, funding (neu perp), gioi han don bay, gioi han drawdown.
- Chot tieu chi pass/fail: Sharpe, Calmar, maxDD, turnover, va bat buoc out-of-sample / walk-forward.

## 3) Sprint R&D 7 ngay (muc tieu: crypto alpha)
- Ngay 1: dung data pipeline + baseline backtest (dam bao khong look-ahead / leakage).
- Ngay 2: tao "thu vien alpha" 10-30 y tuong (time-series + cross-sectional) va test nhanh co chi phi.
- Ngay 3: chon 3-5 ung vien tot nhat, them portfolio construction (ranking, vi the, volatility targeting, constraints).
- Ngay 4: walk-forward + out-of-sample nghiem ngat (purge/embargo neu can), chon tham so theo validation.
- Ngay 5: robustness (phi/slippage x2, regime split, stress test, sensitivity).
- Ngay 6: giam overfit (don gian hoa, regularize, gioi han degrees of freedom), toi uu "tinh ben" hon "dep so".
- Ngay 7: dong goi deliver (CLI chay lai duoc + bao cao + roadmap tuan ke tiep). Neu khong dat Sharpe/Calmar muc tieu, ban giao "best robust" + phan tich vi sao chua dat va plan cai thien.

## 4) Deliverables toi thieu
- Code backtest tai lap (1 lenh chay), config ro gia dinh, ket qua OOS kem benchmark.
- Bao cao: data/gia dinh, phuong phap, ket qua, rui ro, nhung huong da loai bo, ke hoach iteration.

## 5) 1 dong prompt kich hoat (ban nhap)
`Ban la ATLAS, autonomous project commander: trong 7 ngay tu R&D chien luoc quant crypto end-to-end (data->alpha->backtest walk-forward OOS co phi/slippage->robustness->deliver repo+report), muc tieu Sharpe>1.5 va Calmar>2.0 so voi benchmark dinh nghia ro; tu pivot theo bang chung va ghi decision log, khong bia ket qua.`

