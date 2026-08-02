<?php

use PHPUnit\Framework\TestCase;

require_once __DIR__ . '/../src/LoanService.php';

final class LoanServiceTest extends TestCase
{
    /** @return array{0: \App\BookRepository, 1: \App\MemberRepository, 2: \App\LoanRepository, 3: \App\LoanService} */
    private function buildService(): array
    {
        $books = new \App\BookRepository();
        $members = new \App\MemberRepository();
        $loans = new \App\LoanRepository();
        $service = new \App\LoanService($books, $members, $loans);
        return [$books, $members, $loans, $service];
    }

    public function testBorrowDecrementsAvailableCopies(): void
    {
        [$books, $members, $loans, $service] = $this->buildService();
        $book = $books->create('Dune', 'Herbert', 1);
        $member = $members->create('Ada', 'a@example.com');

        $loan = $service->borrow($book->id, $member->id);
        $this->assertSame('active', $loan->status);
        $this->assertSame(0, $books->find($book->id)->availableCopies);
    }

    public function testBorrowThrowsWhenNoCopiesAvailable(): void
    {
        [$books, $members, $loans, $service] = $this->buildService();
        $book = $books->create('Dune', 'Herbert', 1);
        $m1 = $members->create('Ada', 'a@example.com');
        $m2 = $members->create('Bob', 'b@example.com');

        $service->borrow($book->id, $m1->id);
        $this->expectException(\App\LibraryException::class);
        $service->borrow($book->id, $m2->id);
    }

    public function testReturnLoanIncrementsAvailableCopies(): void
    {
        [$books, $members, $loans, $service] = $this->buildService();
        $book = $books->create('Dune', 'Herbert', 1);
        $member = $members->create('Ada', 'a@example.com');

        $loan = $service->borrow($book->id, $member->id);
        $returned = $service->returnLoan($loan->id);

        $this->assertSame('returned', $returned->status);
        $this->assertSame(1, $books->find($book->id)->availableCopies);
    }

    public function testReturnLoanThrowsWhenAlreadyReturned(): void
    {
        [$books, $members, $loans, $service] = $this->buildService();
        $book = $books->create('Dune', 'Herbert', 1);
        $member = $members->create('Ada', 'a@example.com');

        $loan = $service->borrow($book->id, $member->id);
        $service->returnLoan($loan->id);

        $this->expectException(\App\LibraryException::class);
        $service->returnLoan($loan->id);
    }
}
